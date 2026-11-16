"""
Observabilidade estruturada para o AI Video Director (Sprint 2.5 - Tarefa 3).

Responsabilidades:
- Emitir eventos estruturados JSON (started, completed, failed, timeout, fallback, skipped).
- Calcular métricas agregadas sem cardinalidade excessiva (sem job_id/tenant_id como labels).
- Garantir correlação ponta a ponta via correlation_id e execution_id.
- Garantir isolamento multitenant nas consultas de eventos.
- Sanitizar dados sensíveis (nunca registrar roteiro, prompts, tokens, senhas).
- Operar de forma fail-safe: falhas na observabilidade nunca interrompem o pipeline.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

DIRECTOR_VERSION = "2.5.0"

EVENT_STARTED = "director.started"
EVENT_COMPLETED = "director.completed"
EVENT_FAILED = "director.failed"
EVENT_TIMEOUT = "director.timeout"
EVENT_FALLBACK = "director.fallback"
EVENT_SKIPPED = "director.skipped"

ALLOWED_EVENTS = frozenset(
    {
        EVENT_STARTED,
        EVENT_COMPLETED,
        EVENT_FAILED,
        EVENT_TIMEOUT,
        EVENT_FALLBACK,
        EVENT_SKIPPED,
    }
)

_obs_logger = logging.getLogger("cloud_hub.video_director.observability")

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


def sanitize_text(value: Optional[str], max_len: int = 240) -> Optional[str]:
    """Remove credenciais, tokens e segredos de textos livres."""
    if value is None:
        return None
    text = str(value)
    text = _SENSITIVE_URL_CREDS_RE.sub(r"\1[REDACTED]@", text)
    text = _SENSITIVE_BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _SENSITIVE_KV_RE.sub(r"\1\2[REDACTED]", text)
    text = _SENSITIVE_PREFIX_RE.sub("[REDACTED]", text)
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text


def generate_correlation_id(prefix: str = "corr") -> str:
    """Gera um correlation_id único."""
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def generate_execution_id() -> str:
    """Gera um execution_id único para uma execução do Director."""
    return f"exec-{uuid.uuid4().hex[:16]}"


@dataclass(frozen=True)
class DirectorEvent:
    event_name: str
    timestamp: str
    job_id: str
    tenant_id: str
    correlation_id: str
    execution_id: str
    video_director_mode: str
    status: str
    stage: str
    director_version: str = DIRECTOR_VERSION
    duration_ms: Optional[float] = None
    error_code: Optional[str] = None
    fallback_reason: Optional[str] = None
    original_script_restored: Optional[bool] = None
    copywriter_reexecuted: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass
class _MetricSample:
    timestamp_dt: datetime
    event_name: str
    mode: str
    duration_ms: Optional[float]
    error_code: Optional[str]


class VideoDirectorObservabilityCollector:
    """
    Coletor thread-safe de eventos estruturados e métricas agregadas.
    """

    def __init__(self, max_events_per_tenant: int = 1000, max_metric_samples: int = 10000) -> None:
        self._lock = threading.Lock()
        self._events_by_tenant: Dict[str, Deque[Dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=max_events_per_tenant)
        )
        self._metric_samples: Deque[_MetricSample] = deque(maxlen=max_metric_samples)

    def reset(self) -> None:
        """Limpa eventos e métricas armazenados (útil para testes)."""
        with self._lock:
            self._events_by_tenant.clear()
            self._metric_samples.clear()

    def emit_event(
        self,
        *,
        event_name: str,
        job_id: str,
        tenant_id: str,
        correlation_id: str,
        execution_id: str,
        video_director_mode: str,
        status: str,
        stage: str,
        director_version: str = DIRECTOR_VERSION,
        duration_ms: Optional[float] = None,
        error_code: Optional[str] = None,
        fallback_reason: Optional[str] = None,
        original_script_restored: Optional[bool] = None,
        copywriter_reexecuted: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Emite um evento estruturado de forma fail-safe.
        Nunca propaga exceções para o pipeline principal.
        """
        try:
            if event_name not in ALLOWED_EVENTS:
                return None

            now_dt = datetime.now(timezone.utc)
            safe_duration = round(float(duration_ms), 3) if duration_ms is not None else None
            safe_reason = sanitize_text(fallback_reason)
            safe_error_code = sanitize_text(error_code, max_len=64)

            event = DirectorEvent(
                event_name=event_name,
                timestamp=now_dt.isoformat(),
                job_id=str(job_id),
                tenant_id=str(tenant_id),
                correlation_id=str(correlation_id),
                execution_id=str(execution_id),
                video_director_mode=str(video_director_mode),
                status=str(status),
                stage=str(stage),
                director_version=str(director_version),
                duration_ms=safe_duration,
                error_code=safe_error_code,
                fallback_reason=safe_reason,
                original_script_restored=original_script_restored,
                copywriter_reexecuted=copywriter_reexecuted,
            )

            payload = event.to_dict()
            json_line = event.to_json()

            # Emite no logger estruturado
            if event_name in (EVENT_FAILED, EVENT_TIMEOUT, EVENT_FALLBACK):
                _obs_logger.error(json_line)
            else:
                _obs_logger.info(json_line)

            # Armazena em memória isolado por tenant e amostra de métrica sem tenant_id/job_id
            with self._lock:
                self._events_by_tenant[str(tenant_id)].append(payload)
                if event_name != EVENT_STARTED:
                    self._metric_samples.append(
                        _MetricSample(
                            timestamp_dt=now_dt,
                            event_name=event_name,
                            mode=str(video_director_mode),
                            duration_ms=safe_duration,
                            error_code=safe_error_code,
                        )
                    )

            return payload
        except Exception:
            # Fail-safe: observabilidade nunca pode interromper ou mascarar o pipeline
            return None

    def get_events(
        self,
        *,
        tenant_id: str,
        job_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        event_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retorna eventos estritamente filtrados pelo tenant_id informado.
        Garante isolamento multitenant.
        """
        if not tenant_id or not str(tenant_id).strip():
            raise ValueError("tenant_id é obrigatório para consulta de eventos.")

        tenant_key = str(tenant_id)
        with self._lock:
            events = list(self._events_by_tenant.get(tenant_key, ()))

        if job_id is not None:
            events = [e for e in events if e.get("job_id") == str(job_id)]
        if correlation_id is not None:
            events = [e for e in events if e.get("correlation_id") == str(correlation_id)]
        if event_name is not None:
            events = [e for e in events if e.get("event_name") == event_name]

        return events

    def get_metrics(
        self,
        *,
        mode: Optional[str] = None,
        since_utc: Optional[datetime] = None,
        until_utc: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Calcula métricas agregadas sem labels de alta cardinalidade (sem job_id/tenant_id).
        Permite filtrar por modo de execução e período UTC.
        """
        with self._lock:
            samples = list(self._metric_samples)

        if mode is not None:
            samples = [s for s in samples if s.mode == mode]
        if since_utc is not None:
            samples = [s for s in samples if s.timestamp_dt >= since_utc]
        if until_utc is not None:
            samples = [s for s in samples if s.timestamp_dt <= until_utc]

        completed = sum(1 for s in samples if s.event_name == EVENT_COMPLETED)
        failed = sum(1 for s in samples if s.event_name == EVENT_FAILED)
        timed_out = sum(1 for s in samples if s.event_name == EVENT_TIMEOUT)
        fallbacks = sum(1 for s in samples if s.event_name == EVENT_FALLBACK)
        skipped = sum(1 for s in samples if s.event_name == EVENT_SKIPPED)

        # Cada execução terminal gera completed, skipped, ou (failed/timeout + fallback)
        # Contamos execuções únicas excluindo o evento secundário fallback para não contar 2x
        total_executions = completed + failed + timed_out + skipped
        fallback_rate = round(fallbacks / total_executions, 4) if total_executions > 0 else 0.0

        durations = [
            s.duration_ms
            for s in samples
            if s.duration_ms is not None and s.event_name != EVENT_FALLBACK
        ]
        avg_duration_ms = round(sum(durations) / len(durations), 3) if durations else 0.0
        max_duration_ms = round(max(durations), 3) if durations else 0.0

        duration_by_mode_acc: Dict[str, List[float]] = defaultdict(list)
        for s in samples:
            if s.duration_ms is not None and s.event_name != EVENT_FALLBACK:
                duration_by_mode_acc[s.mode].append(s.duration_ms)

        duration_by_mode: Dict[str, Dict[str, Any]] = {}
        for m, vals in duration_by_mode_acc.items():
            duration_by_mode[m] = {
                "count": len(vals),
                "avg_duration_ms": round(sum(vals) / len(vals), 3),
                "max_duration_ms": round(max(vals), 3),
            }

        failures_by_error_code: Dict[str, int] = defaultdict(int)
        for s in samples:
            if s.event_name in (EVENT_FAILED, EVENT_TIMEOUT):
                code = s.error_code or "UNKNOWN"
                failures_by_error_code[code] += 1

        return {
            "total_executions": total_executions,
            "completed_executions": completed,
            "failed_executions": failed,
            "timeout_executions": timed_out,
            "skipped_executions": skipped,
            "fallback_count": fallbacks,
            "fallback_rate": fallback_rate,
            "avg_duration_ms": avg_duration_ms,
            "max_duration_ms": max_duration_ms,
            "duration_by_mode": duration_by_mode,
            "failures_by_error_code": dict(failures_by_error_code),
        }


# Instância padrão compartilhada (thread-safe)
default_observability_collector = VideoDirectorObservabilityCollector()
