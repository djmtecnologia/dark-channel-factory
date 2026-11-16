"""
Camada de integração controlada do AI Video Director no Cloud Hub (Sprint 2.5 - Tarefa 2).

Responsabilidades:
- Resolver a feature flag VIDEO_DIRECTOR_MODE com precedência: job > env > default ('legacy').
- Rejeitar valores inválidos explicitamente.
- Executar AIVideoDirector + MasterPlanAdapter quando em modo 'director'.
- Garantir isolamento multitenant (sem estado global mutável).
- Executar fallback seguro para 'legacy_fallback' preservando o script_data original do Copywriter.
- Registrar observabilidade estruturada sem expor credenciais ou dados sensíveis.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import time
from typing import Any, Callable, Dict, Optional, Tuple

from agents.video_director.director_engine import AIVideoDirector, AIVideoDirectorError
from agents.video_director.pipeline_adapter import (
    MasterPlanAdapter,
    MasterPlanAdapterError,
    MasterPlanDataLossError,
)
from cloud_hub.core.video_director_observability import (
    DIRECTOR_VERSION,
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_FALLBACK,
    EVENT_SKIPPED,
    EVENT_STARTED,
    EVENT_TIMEOUT,
    VideoDirectorObservabilityCollector,
    default_observability_collector,
    generate_correlation_id,
    generate_execution_id,
)

logger = logging.getLogger(__name__)

ALLOWED_VIDEO_DIRECTOR_MODES = ("legacy", "director")
DEFAULT_VIDEO_DIRECTOR_MODE = "legacy"
ENV_VIDEO_DIRECTOR_MODE = "VIDEO_DIRECTOR_MODE"


class InvalidVideoDirectorModeError(ValueError):
    """Erro levantado quando VIDEO_DIRECTOR_MODE recebe um valor inválido."""


class VideoDirectorIntegrationError(RuntimeError):
    """Erro controlado quando a integração falha e o script original não é recuperável."""


def _normalize_mode_value(raw_value: Optional[str], source: str) -> Optional[str]:
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        raise InvalidVideoDirectorModeError(
            f"VIDEO_DIRECTOR_MODE inválido em '{source}': esperado string, recebido {type(raw_value).__name__}"
        )
    cleaned = raw_value.strip().lower()
    if cleaned == "":
        return None
    if cleaned not in ALLOWED_VIDEO_DIRECTOR_MODES:
        raise InvalidVideoDirectorModeError(
            f"VIDEO_DIRECTOR_MODE inválido em '{source}': '{raw_value}'. "
            f"Valores permitidos: {', '.join(ALLOWED_VIDEO_DIRECTOR_MODES)}"
        )
    return cleaned


def resolve_video_director_mode(
    job: Any = None,
    explicit_mode: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Resolve o modo de execução do AI Video Director.

    Precedência:
    1. explicit_mode, quando fornecido por fluxo confiável;
    2. job.video_director_mode (coluna dedicada no modelo VideoJob);
    3. Variável de ambiente VIDEO_DIRECTOR_MODE;
    4. Default obrigatório: 'legacy'.

    Nota de segurança/arquitetura:
    - job.script_data e job.performance_metrics NÃO são lidos para resolução da flag.

    Retorna:
        Tuple[mode, source] onde source é 'job', 'env' ou 'default'.
    """
    # 1. explicit_mode (fluxo confiável) ou coluna dedicada job.video_director_mode.
    # Nunca ler de job.script_data nem de job.performance_metrics.
    job_mode_raw = explicit_mode
    if job_mode_raw is None and job is not None:
        job_mode_raw = getattr(job, "video_director_mode", None)

    job_mode = _normalize_mode_value(job_mode_raw, "job")
    if job_mode is not None:
        return job_mode, "job"

    # 2. Configuração global do ambiente
    env_mode_raw = os.getenv(ENV_VIDEO_DIRECTOR_MODE)
    env_mode = _normalize_mode_value(env_mode_raw, "env")
    if env_mode is not None:
        return env_mode, "env"

    # 3. Default seguro
    return DEFAULT_VIDEO_DIRECTOR_MODE, "default"


_SENSITIVE_URL_CREDS_RE = re.compile(r"(https?://)([^/\s:@]+:[^/\s@]+@)", re.IGNORECASE)
_SENSITIVE_KV_RE = re.compile(
    r"(?i)\b(api[_-]?key|x-api-key|token|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"secret|client[_-]?secret|password|passwd|pwd|authorization|cookie|set-cookie|private[_-]?key)"
    r"(\s*[=:]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
_SENSITIVE_BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9\-._~+/]+=*")
_SENSITIVE_PREFIX_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z\-_]{10,}|xai-[A-Za-z0-9_\-]{8,}|"
    r"gsk_[A-Za-z0-9_\-]{8,}|hf_[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}|gho_[A-Za-z0-9]{8,}|"
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]+)?)"
)


_SAFE_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
_UNSAFE_CODE_PREFIX_RE = re.compile(r"^(?:sk-|AIza|xai-|gsk_|hf_|ghp_|gho_|eyJ)", re.IGNORECASE)


def _extract_error_code(exc: BaseException) -> Optional[str]:
    """
    Extrai código de erro técnico da exceção, validando formato seguro e tamanho.
    Rejeita valores com credenciais, URLs, espaços ou prefixos de tokens.
    """
    for attr in ("status_code", "code", "error_code"):
        val = getattr(exc, attr, None)
        if val is None:
            continue
        candidate = str(val).strip()
        if not candidate or len(candidate) > 32:
            continue
        if _UNSAFE_CODE_PREFIX_RE.match(candidate):
            continue
        if _SAFE_ERROR_CODE_RE.match(candidate):
            return candidate
    return None


def sanitize_error_message(exc: BaseException, max_length: int = 240) -> str:
    """
    Sanitiza mensagens de exceção removendo credenciais, tokens, senhas,
    URLs autenticadas e payloads extensos, preservando a informação técnica.
    """
    raw = str(exc) if exc is not None else ""
    sanitized = " ".join(raw.split())
    sanitized = _SENSITIVE_URL_CREDS_RE.sub(r"\1[REDACTED]@", sanitized)
    sanitized = _SENSITIVE_BEARER_RE.sub(r"\1[REDACTED]", sanitized)
    sanitized = _SENSITIVE_KV_RE.sub(r"\1\2[REDACTED]", sanitized)
    sanitized = _SENSITIVE_PREFIX_RE.sub("[REDACTED]", sanitized)
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "...[truncated]"
    return sanitized or type(exc).__name__


def _is_valid_legacy_script_data(script_data: Any) -> bool:
    """Verifica se script_data atende ao contrato mínimo operacional do Pipeline B."""
    if not isinstance(script_data, dict):
        return False
    scenes = script_data.get("scenes")
    if not isinstance(scenes, list) or len(scenes) == 0:
        return False
    for scene in scenes:
        if not isinstance(scene, dict):
            return False
        narration = scene.get("narration") or scene.get("text")
        if not isinstance(narration, str) or not narration.strip():
            return False
    return True


def integrate_video_director(
    job: Any,
    tenant_id: Optional[Any] = None,
    explicit_mode: Optional[str] = None,
    director_factory: Optional[Callable[[], AIVideoDirector]] = None,
    adapter_factory: Optional[Callable[[], MasterPlanAdapter]] = None,
    correlation_id: Optional[str] = None,
    observability_collector: Optional[VideoDirectorObservabilityCollector] = None,
) -> Dict[str, Any]:
    """
    Aplica a integração controlada do AI Video Director sobre job.script_data.

    - Em modo 'legacy': não executa Director/Adapter e preserva script_data intacto.
    - Em modo 'director': executa AIVideoDirector -> MasterPlanAdapter (com validação anti-perda).
    - Em caso de falha no modo 'director': executa fallback seguro para 'legacy_fallback'
      se o script_data original do Copywriter for válido.
    """
    integration_start = time.monotonic()
    job_id = str(getattr(job, "id", "unknown"))
    tenant_str = str(tenant_id) if tenant_id is not None else "unknown"
    corr_id = correlation_id or generate_correlation_id(prefix=f"job-{job_id}")
    exec_id = generate_execution_id()
    collector = observability_collector or default_observability_collector

    mode, mode_source = resolve_video_director_mode(job=job, explicit_mode=explicit_mode)

    logger.info(
        "video_director_mode_resolved job_id=%s tenant_id=%s video_director_mode=%s mode_source=%s",
        job_id,
        tenant_str,
        mode,
        mode_source,
    )

    if mode == "legacy":
        total_duration = round(time.monotonic() - integration_start, 6)
        duration_ms = round(total_duration * 1000.0, 3)
        collector.emit_event(
            event_name=EVENT_SKIPPED,
            job_id=job_id,
            tenant_id=tenant_str,
            correlation_id=corr_id,
            execution_id=exec_id,
            video_director_mode="legacy",
            status="skipped",
            stage="mode_resolution",
            director_version=DIRECTOR_VERSION,
            duration_ms=duration_ms,
        )
        return {
            "job_id": job_id,
            "tenant_id": tenant_str,
            "correlation_id": corr_id,
            "execution_id": exec_id,
            "director_version": DIRECTOR_VERSION,
            "video_director_mode": "legacy",
            "mode_source": mode_source,
            "director_execution_started": False,
            "director_execution_completed": False,
            "adapter_validation_passed": False,
            "fallback_triggered": False,
            "fallback_reason": None,
            "duration_seconds": total_duration,
            "duration_ms": duration_ms,
        }

    # Modo 'director': preserva cópia imutável do resultado original do Copywriter
    original_script_data = copy.deepcopy(getattr(job, "script_data", None))
    if not _is_valid_legacy_script_data(original_script_data):
        raise VideoDirectorIntegrationError(
            f"script_data original inválido para execução do AI Video Director (job_id={job_id}, tenant_id={tenant_str})"
        )

    stage = "director_init"
    director_started = False
    director_completed = False
    adapter_passed = False
    stage_durations: Dict[str, float] = {}

    try:
        director_started = True
        collector.emit_event(
            event_name=EVENT_STARTED,
            job_id=job_id,
            tenant_id=tenant_str,
            correlation_id=corr_id,
            execution_id=exec_id,
            video_director_mode=mode,
            status="started",
            stage="director_init",
            director_version=DIRECTOR_VERSION,
        )
        logger.info(
            "director_execution_started job_id=%s tenant_id=%s video_director_mode=%s mode_source=%s",
            job_id,
            tenant_str,
            mode,
            mode_source,
        )

        # Instância isolada por execução/tenant (sem estado global compartilhado)
        stage = "director_engine"
        t0 = time.monotonic()
        director = director_factory() if director_factory is not None else AIVideoDirector()
        production_profile = getattr(job, "production_profile", None) or "PILOT_5MIN"
        master_plan = director.create_master_plan(
            script_data=original_script_data,
            production_profile=production_profile,
        )
        stage_durations["director_engine"] = round(time.monotonic() - t0, 6)
        director_completed = True

        logger.info(
            "director_execution_completed job_id=%s tenant_id=%s duration_seconds=%s",
            job_id,
            tenant_str,
            stage_durations["director_engine"],
        )

        # Fronteira oficial de conversão + validação anti-perda
        stage = "master_plan_adapter"
        t1 = time.monotonic()
        adapter = adapter_factory() if adapter_factory is not None else MasterPlanAdapter()
        adapted_payload = adapter.to_pipeline_b(master_plan)
        stage_durations["master_plan_adapter"] = round(time.monotonic() - t1, 6)

        if not adapted_payload.scenes:
            raise MasterPlanDataLossError("MasterPlanAdapter retornou lista de cenas vazia.")

        adapter_passed = True
        logger.info(
            "adapter_validation_passed job_id=%s tenant_id=%s scenes_count=%s duration_seconds=%s",
            job_id,
            tenant_str,
            len(adapted_payload.scenes),
            stage_durations["master_plan_adapter"],
        )

        # Monta payload final preservando integralmente os campos legados do Pipeline B
        updated_script_data = copy.deepcopy(original_script_data)
        updated_script_data["scenes"] = adapted_payload.scenes
        updated_script_data["director_metadata"] = adapted_payload.director_metadata
        updated_script_data["master_av_plan_summary"] = adapted_payload.master_av_plan_summary
        updated_script_data["preserved_fields_report"] = adapted_payload.preserved_fields_report
        updated_script_data["video_director_mode"] = "director"
        updated_script_data["mode_source"] = mode_source
        updated_script_data["adapter_validation_passed"] = True
        updated_script_data["fallback_triggered"] = False

        job.script_data = updated_script_data

        total_duration = round(time.monotonic() - integration_start, 6)
        duration_ms = round(total_duration * 1000.0, 3)
        collector.emit_event(
            event_name=EVENT_COMPLETED,
            job_id=job_id,
            tenant_id=tenant_str,
            correlation_id=corr_id,
            execution_id=exec_id,
            video_director_mode="director",
            status="completed",
            stage="master_plan_adapter",
            director_version=DIRECTOR_VERSION,
            duration_ms=duration_ms,
        )
        return {
            "job_id": job_id,
            "tenant_id": tenant_str,
            "correlation_id": corr_id,
            "execution_id": exec_id,
            "director_version": DIRECTOR_VERSION,
            "video_director_mode": "director",
            "mode_source": mode_source,
            "director_execution_started": director_started,
            "director_execution_completed": director_completed,
            "adapter_validation_passed": adapter_passed,
            "fallback_triggered": False,
            "fallback_reason": None,
            "duration_seconds": total_duration,
            "duration_ms": duration_ms,
            "stage_durations": stage_durations,
        }

    except (AIVideoDirectorError, MasterPlanAdapterError, Exception) as exc:
        if isinstance(exc, AIVideoDirectorError) and getattr(exc, "stage", None):
            stage = exc.stage
        error_code = _extract_error_code(exc)
        sanitized_msg = sanitize_error_message(exc)
        if error_code is not None:
            reason = f"{stage}: {type(exc).__name__} (code={error_code}): {sanitized_msg}"
        else:
            reason = f"{stage}: {type(exc).__name__}: {sanitized_msg}"

        logger.error(
            "video_director_fallback_triggered job_id=%s tenant_id=%s stage=%s fallback_triggered=True fallback_reason=%s",
            job_id,
            tenant_str,
            stage,
            reason,
        )

        # Fallback seguro: restaura o script_data original do Copywriter sem reexecutá-lo
        if not _is_valid_legacy_script_data(original_script_data):
            raise VideoDirectorIntegrationError(
                f"Falha no Director ({reason}) e script_data original inválido para fallback."
            ) from exc

        fallback_script_data = copy.deepcopy(original_script_data)
        fallback_script_data["video_director_mode"] = "legacy_fallback"
        fallback_script_data["mode_source"] = mode_source
        fallback_script_data["adapter_validation_passed"] = False
        fallback_script_data["fallback_triggered"] = True
        fallback_script_data["fallback_reason"] = reason
        job.script_data = fallback_script_data

        total_duration = round(time.monotonic() - integration_start, 6)
        duration_ms = round(total_duration * 1000.0, 3)

        is_timeout = isinstance(exc, TimeoutError) or (
            error_code is not None and "TIMEOUT" in error_code.upper()
        )
        failure_event = EVENT_TIMEOUT if is_timeout else EVENT_FAILED
        failure_status = "timeout" if is_timeout else "failed"

        collector.emit_event(
            event_name=failure_event,
            job_id=job_id,
            tenant_id=tenant_str,
            correlation_id=corr_id,
            execution_id=exec_id,
            video_director_mode="director",
            status=failure_status,
            stage=stage,
            director_version=DIRECTOR_VERSION,
            duration_ms=duration_ms,
            error_code=error_code or type(exc).__name__,
            fallback_reason=reason,
        )
        collector.emit_event(
            event_name=EVENT_FALLBACK,
            job_id=job_id,
            tenant_id=tenant_str,
            correlation_id=corr_id,
            execution_id=exec_id,
            video_director_mode="legacy_fallback",
            status="fallback",
            stage=stage,
            director_version=DIRECTOR_VERSION,
            duration_ms=duration_ms,
            error_code=error_code or type(exc).__name__,
            fallback_reason=reason,
            original_script_restored=True,
            copywriter_reexecuted=False,
        )

        return {
            "job_id": job_id,
            "tenant_id": tenant_str,
            "correlation_id": corr_id,
            "execution_id": exec_id,
            "director_version": DIRECTOR_VERSION,
            "video_director_mode": "legacy_fallback",
            "mode_source": mode_source,
            "director_execution_started": director_started,
            "director_execution_completed": director_completed,
            "adapter_validation_passed": False,
            "fallback_triggered": True,
            "fallback_reason": reason,
            "original_script_restored": True,
            "copywriter_reexecuted": False,
            "duration_seconds": total_duration,
            "duration_ms": duration_ms,
            "stage_durations": stage_durations,
        }
