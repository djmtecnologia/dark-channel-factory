"""
Testes da Integração Controlada do AI Video Director no Cloud Hub (Sprint 2.5 - Tarefa 2).

Cobre:
- 11.1 Feature flag (precedência job > env > default, rejeição de inválidos)
- 11.2 Fluxo legacy (Copywriter 1x, Director 0x, contrato Desktop Node preservado)
- 11.3 Fluxo director (Copywriter 1x, Director 1x, Adapter 1x, anti-perda, narração preservada)
- 11.4 Fallback seguro (falha em cada etapa + script original inválido)
- 11.5 Multitenancy (Tenant A director vs Tenant B legacy em execuções simultâneas)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

import pytest

from agents.video_director.director_engine import AIVideoDirector, AIVideoDirectorError
from agents.video_director.pipeline_adapter import (
    MasterPlanAdapter,
    MasterPlanAdapterError,
    MasterPlanDataLossError,
)
from cloud_hub.core.video_director_integration import (
    InvalidVideoDirectorModeError,
    VideoDirectorIntegrationError,
    integrate_video_director,
    resolve_video_director_mode,
)


def _make_sample_script_data(topic: str = "AI Secrets", mode_override: str | None = None) -> dict:
    data = {
        "title": topic,
        "marketing_strategy": {"hook": "shocking"},
        "description": "Full description",
        "tags": ["ai", "tech"],
        "script_full": "Scene one narration.\nScene two narration.",
        "scenes": [
            {
                "id": 1,
                "scene_id": 1,
                "order": 1,
                "timestamp": "0:00-2:30",
                "duration_s": 150.0,
                "narration": "Scene one narration.",
                "b_roll_visual": "dark server room",
            },
            {
                "id": 2,
                "scene_id": 2,
                "order": 2,
                "timestamp": "2:30-5:00",
                "duration_s": 150.0,
                "narration": "Scene two narration.",
                "b_roll_visual": "glowing neural network",
            },
        ],
        "music_mood": "suspense",
        "language": "en-US",
        "cta_link": "https://example.com/offer",
        "quiz": None,
        "generated_at": "2026-11-14T18:00:00+00:00",
    }
    if mode_override is not None:
        data["video_director_mode"] = mode_override
    return data


def _make_job(
    topic: str = "AI Secrets",
    mode_override: str | None = None,
    production_profile: str = "PILOT_5MIN",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        topic=topic,
        production_profile=production_profile,
        video_director_mode=mode_override,
        script_data=_make_sample_script_data(topic=topic),
    )


class _FakeLLMClient:
    """Cliente LLM determinístico para testes sem chamadas externas."""

    def __init__(self):
        self._call_index = 0

    def complete(self, *args, timeout: float = 30.0, **kwargs):
        import json

        self._call_index += 1
        if self._call_index == 3:
            return json.dumps(
                {
                    "scenes": [
                        {
                            "scene_id": 1,
                            "order": 1,
                            "narrative_role": "hook",
                            "visual_intent": "Establish dark tech mood",
                            "start_time": 0.0,
                            "end_time": 150.0,
                            "duration_s": 150.0,
                            "sfx_cue": "low_drone",
                            "on_screen_text": "",
                            "estimated_cost_tier": "low",
                            "transition_rationale": "Smooth fade in",
                            "continuity_notes": "Keep dark tone",
                            "planning_rationale": "Strong opening hook",
                        },
                        {
                            "scene_id": 2,
                            "order": 2,
                            "narrative_role": "context",
                            "visual_intent": "Deepen technical mystery",
                            "start_time": 150.0,
                            "end_time": 300.0,
                            "duration_s": 150.0,
                            "sfx_cue": "subtle_riser",
                            "on_screen_text": "",
                            "estimated_cost_tier": "low",
                            "transition_rationale": "Cross dissolve",
                            "continuity_notes": "Maintain blue glow",
                            "planning_rationale": "Build tension",
                        },
                    ]
                }
            )

        if self._call_index == 2:
            return json.dumps(
                {
                    "visual_bible": {
                        "visual_language": "cinematic",
                        "dominant_style": "dark documentary",
                        "color_palette": ["#0D1B2A", "#00B4D8"],
                        "lighting_mood": "low_key",
                        "pacing_rhythm": "deliberate",
                    },
                    "scene_directions": [
                        {
                            "scene_id": 1,
                            "what_viewer_sees": "A dimly lit server room with blinking blue LEDs",
                            "dominant_element": "Server racks",
                            "visual_production_type": "stock_footage",
                            "b_roll_query": "dark server room cinematic",
                            "camera_framing": "medium",
                            "camera_movement": "slow_push_in",
                            "lighting_mood": "low_key",
                            "transition_in": "fade_in",
                            "transition_out": "cross_dissolve",
                            "continuity_anchor": "server_rack_light",
                        },
                        {
                            "scene_id": 2,
                            "what_viewer_sees": "Glowing abstract neural network connections",
                            "dominant_element": "Neural nodes",
                            "visual_production_type": "stock_footage",
                            "b_roll_query": "glowing neural network abstract",
                            "camera_framing": "close_up",
                            "camera_movement": "static",
                            "lighting_mood": "neon_dark",
                            "transition_in": "cross_dissolve",
                            "transition_out": "fade_out",
                            "continuity_anchor": "blue_glow",
                        },
                    ],
                }
            )

        return json.dumps(
            {
                "overall_narrative_arc": "hook -> development -> climax",
                "narrative_progression": ["hook", "context", "revelation"],
                "core_theme": "AI Secrets",
                "target_emotional_journey": ["curiosity", "tension"],
                "scene_analyses": [
                    {
                        "scene_id": 1,
                        "narrative_purpose": "hook",
                        "scene_intent": "Grab viewer attention immediately",
                    },
                    {
                        "scene_id": 2,
                        "narrative_purpose": "context",
                        "scene_intent": "Deepen the core explanation",
                    },
                ],
            }
        )


# =============================================================================
# 11.1 FEATURE FLAG
# =============================================================================


def test_feature_flag_default_is_legacy(monkeypatch):
    monkeypatch.delenv("VIDEO_DIRECTOR_MODE", raising=False)
    job = _make_job()
    mode, source = resolve_video_director_mode(job=job)
    assert mode == "legacy"
    assert source == "default"


def test_feature_flag_env_selects_director(monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_MODE", "director")
    job = _make_job()
    mode, source = resolve_video_director_mode(job=job)
    assert mode == "director"
    assert source == "env"


def test_feature_flag_job_overrides_env(monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_MODE", "director")
    job = _make_job(mode_override="legacy")
    mode, source = resolve_video_director_mode(job=job)
    assert mode == "legacy"
    assert source == "job"


def test_feature_flag_invalid_value_rejected(monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_MODE", "turbo_mode")
    job = _make_job()
    with pytest.raises(InvalidVideoDirectorModeError):
        resolve_video_director_mode(job=job)

    monkeypatch.delenv("VIDEO_DIRECTOR_MODE", raising=False)
    bad_job = _make_job(mode_override="invalid_job_mode")
    with pytest.raises(InvalidVideoDirectorModeError):
        resolve_video_director_mode(job=bad_job)


def test_feature_flag_empty_never_activates_director(monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_MODE", "   ")
    job = _make_job(mode_override="")
    mode, source = resolve_video_director_mode(job=job)
    assert mode == "legacy"
    assert source == "default"


# =============================================================================
# 11.2 FLUXO LEGACY
# =============================================================================


def test_legacy_flow_does_not_run_director_and_preserves_payload(monkeypatch):
    monkeypatch.delenv("VIDEO_DIRECTOR_MODE", raising=False)
    job = _make_job()
    original_scenes = [dict(s) for s in job.script_data["scenes"]]

    director_factory = MagicMock()
    adapter_factory = MagicMock()

    result = integrate_video_director(
        job=job,
        tenant_id="tenant-legacy",
        director_factory=director_factory,
        adapter_factory=adapter_factory,
    )

    assert result["video_director_mode"] == "legacy"
    assert result["director_execution_started"] is False
    assert result["director_execution_completed"] is False
    assert result["adapter_validation_passed"] is False
    assert result["fallback_triggered"] is False
    director_factory.assert_not_called()
    adapter_factory.assert_not_called()
    assert job.script_data["scenes"] == original_scenes


# =============================================================================
# 11.3 FLUXO DIRECTOR
# =============================================================================


def test_director_flow_executes_director_and_adapter_and_preserves_narration(monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_MODE", "director")
    job = _make_job()
    original_narrations = [s["narration"] for s in job.script_data["scenes"]]

    director_calls = {"count": 0}
    adapter_calls = {"count": 0}

    def _director_factory():
        director_calls["count"] += 1
        return AIVideoDirector(llm_client=_FakeLLMClient())

    def _adapter_factory():
        adapter_calls["count"] += 1
        return MasterPlanAdapter()

    result = integrate_video_director(
        job=job,
        tenant_id="tenant-director",
        director_factory=_director_factory,
        adapter_factory=_adapter_factory,
    )

    assert result["video_director_mode"] == "director"
    assert result["director_execution_started"] is True
    assert result["director_execution_completed"] is True
    assert result["adapter_validation_passed"] is True
    assert result["fallback_triggered"] is False
    assert director_calls["count"] == 1
    assert adapter_calls["count"] == 1

    # Narração original preservada e payload compatível com Pipeline B
    assert [s["narration"] for s in job.script_data["scenes"]] == original_narrations
    assert "master_av_plan_summary" in job.script_data
    assert "director_metadata" in job.script_data
    assert job.script_data["title"] == "AI Secrets"
    assert job.script_data["cta_link"] == "https://example.com/offer"


# =============================================================================
# 11.4 FALLBACK SEGURO
# =============================================================================


@pytest.mark.parametrize(
    "stage_name,exc_factory",
    [
        ("narrative_analyzer", lambda: AIVideoDirectorError("narrative_analyzer", "ERR_NA", "Analyzer failed")),
        ("visual_director", lambda: AIVideoDirectorError("visual_director", "ERR_VD", "Visual failed")),
        ("scene_planner", lambda: AIVideoDirectorError("scene_planner", "ERR_SP", "Planner failed")),
        ("director_engine", lambda: AIVideoDirectorError("director_engine", "ERR_DE", "Engine failed")),
        ("master_plan_adapter", lambda: MasterPlanAdapterError("Adapter failed")),
        ("anti_loss_validation", lambda: MasterPlanDataLossError("Data loss detected")),
    ],
)
def test_fallback_on_each_stage_failure_preserves_original_script(monkeypatch, stage_name, exc_factory):
    monkeypatch.setenv("VIDEO_DIRECTOR_MODE", "director")
    job = _make_job()
    original_scenes = [dict(s) for s in job.script_data["scenes"]]

    if stage_name in ("narrative_analyzer", "visual_director", "scene_planner", "director_engine"):
        fake_director = MagicMock()
        fake_director.create_master_plan.side_effect = exc_factory()
        director_factory = lambda: fake_director
        adapter_factory = MasterPlanAdapter
    else:
        director_factory = lambda: AIVideoDirector(llm_client=_FakeLLMClient())
        fake_adapter = MagicMock()
        fake_adapter.to_pipeline_b.side_effect = exc_factory()
        adapter_factory = lambda: fake_adapter

    result = integrate_video_director(
        job=job,
        tenant_id="tenant-fallback",
        director_factory=director_factory,
        adapter_factory=adapter_factory,
    )

    assert result["video_director_mode"] == "legacy_fallback"
    assert result["fallback_triggered"] is True
    assert result["adapter_validation_passed"] is False
    assert result["fallback_reason"] is not None
    assert job.script_data["video_director_mode"] == "legacy_fallback"
    assert job.script_data["scenes"] == original_scenes


def test_fallback_propagates_error_when_original_script_is_invalid(monkeypatch):
    monkeypatch.setenv("VIDEO_DIRECTOR_MODE", "director")
    job = SimpleNamespace(
        id=uuid.uuid4(),
        topic="Broken",
        production_profile="PILOT_5MIN",
        script_data={"title": "Broken", "scenes": []},
    )

    with pytest.raises(VideoDirectorIntegrationError):
        integrate_video_director(job=job, tenant_id="tenant-broken")


# =============================================================================
# 11.5 MULTITENANCY
# =============================================================================


def test_multitenant_isolation_concurrent_execution(monkeypatch):
    monkeypatch.delenv("VIDEO_DIRECTOR_MODE", raising=False)

    job_a = _make_job(topic="Tenant A Topic", mode_override="director")
    job_b = _make_job(topic="Tenant B Topic", mode_override="legacy")

    def run_for_tenant_a():
        return integrate_video_director(
            job=job_a,
            tenant_id="tenant-A",
            director_factory=lambda: AIVideoDirector(llm_client=_FakeLLMClient()),
            adapter_factory=MasterPlanAdapter,
        )

    def run_for_tenant_b():
        return integrate_video_director(
            job=job_b,
            tenant_id="tenant-B",
            director_factory=lambda: AIVideoDirector(llm_client=_FakeLLMClient()),
            adapter_factory=MasterPlanAdapter,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(run_for_tenant_a)
        fut_b = pool.submit(run_for_tenant_b)
        res_a = fut_a.result()
        res_b = fut_b.result()

    assert res_a["video_director_mode"] == "director"
    assert res_a["tenant_id"] == "tenant-A"
    assert "master_av_plan_summary" in job_a.script_data

    assert res_b["video_director_mode"] == "legacy"
    assert res_b["tenant_id"] == "tenant-B"


def test_legacy_mode_preserves_script_data_byte_for_byte(monkeypatch):
    """No modo legacy, job.script_data deve permanecer rigorosamente inalterado."""
    import copy

    monkeypatch.delenv("VIDEO_DIRECTOR_MODE", raising=False)
    job = _make_job(topic="Legacy Preservation")
    before = copy.deepcopy(job.script_data)

    obs = integrate_video_director(job=job, tenant_id="tenant-legacy")

    assert obs["video_director_mode"] == "legacy"
    assert obs["mode_source"] == "default"
    assert job.script_data == before
    assert "video_director_mode" not in job.script_data
    assert "mode_source" not in job.script_data


def test_arbitrary_script_data_cannot_activate_director(monkeypatch):
    """Conteúdo arbitrário em script_data['video_director_mode'] não deve ativar o Director."""
    monkeypatch.delenv("VIDEO_DIRECTOR_MODE", raising=False)
    job = SimpleNamespace(
        id=uuid.uuid4(),
        topic="Injection Test",
        production_profile="PILOT_5MIN",
        script_data=_make_sample_script_data(topic="Injection Test", mode_override="director"),
    )

    obs = integrate_video_director(job=job, tenant_id="tenant-sec")

    assert obs["video_director_mode"] == "legacy"
    assert obs["mode_source"] == "default"
    assert obs["director_execution_started"] is False


def test_fallback_sanitizes_credentials_in_logs_and_reason(caplog):
    """Segredos em exceções nunca devem aparecer nos logs, fallback_reason ou script_data."""
    import logging

    class _ProviderAuthError(RuntimeError):
        def __init__(self, message: str, status_code: int = 401):
            super().__init__(message)
            self.status_code = status_code

    class _LeakingDirector:
        def create_master_plan(self, *args, **kwargs):
            raise _ProviderAuthError(
                "Auth failed at https://admin:supersecretpass@api.provider.com/v1/chat "
                "with api_key=sk-proj-1234567890abcdef and Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig "
                "token=ghp_secretToken999999",
                status_code=401,
            )

    job = _make_job(mode_override="director")
    with caplog.at_level(logging.ERROR):
        obs = integrate_video_director(
            job=job,
            tenant_id="tenant-san",
            director_factory=_LeakingDirector,
        )

    assert obs["video_director_mode"] == "legacy_fallback"
    assert obs["fallback_triggered"] is True
    reason = obs["fallback_reason"]

    # Preserva etapa, classe, código e mensagem técnica sanitizada
    assert "director_engine" in reason
    assert "_ProviderAuthError" in reason
    assert "code=401" in reason
    assert "Auth failed" in reason

    # Nenhum segredo pode vazar em reason, script_data ou logs
    secrets = [
        "supersecretpass",
        "sk-proj-1234567890abcdef",
        "eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "ghp_secretToken999999",
    ]
    for secret in secrets:
        assert secret not in reason
        assert secret not in job.script_data["fallback_reason"]
        assert secret not in caplog.text


def test_fallback_sanitizes_malicious_error_codes_and_preserves_safe_codes(caplog):
    """Códigos de erro maliciosos em status_code/code/error_code não devem vazar."""
    import logging

    class _MaliciousCodeError(RuntimeError):
        def __init__(self, msg: str, status_code=None, code=None, error_code=None):
            super().__init__(msg)
            self.status_code = status_code
            self.code = code
            self.error_code = error_code

    malicious_cases = [
        {"status_code": "sk-proj-secret1234567890", "code": None, "error_code": None},
        {"status_code": None, "code": "https://u:p@evil.com/steal", "error_code": None},
        {"status_code": None, "code": None, "error_code": "ghp_superSecretToken99999"},
        {"status_code": "A" * 40, "code": None, "error_code": None},
    ]

    for case in malicious_cases:
        caplog.clear()
        job = _make_job(mode_override="director")
        with caplog.at_level(logging.ERROR):
            obs = integrate_video_director(
                job=job,
                tenant_id="tenant-code-sec",
                director_factory=lambda c=case: SimpleNamespace(
                    create_master_plan=lambda *a, **kw: (_ for _ in ()).throw(
                        _MaliciousCodeError("Provider failure", **c)
                    )
                ),
            )
        assert obs["video_director_mode"] == "legacy_fallback"
        for val in case.values():
            if val:
                assert val not in obs["fallback_reason"]
                assert val not in job.script_data["fallback_reason"]
                assert val not in caplog.text

    # Códigos técnicos seguros devem ser preservados
    for safe_code in (429, "RATE_LIMIT"):
        job_ok = _make_job(mode_override="director")
        obs_ok = integrate_video_director(
            job=job_ok,
            tenant_id="tenant-code-ok",
            director_factory=lambda sc=safe_code: SimpleNamespace(
                create_master_plan=lambda *a, **kw: (_ for _ in ()).throw(
                    _MaliciousCodeError("Rate limited", status_code=sc)
                )
            ),
        )
        assert f"code={safe_code}" in obs_ok["fallback_reason"]


def test_real_video_job_persistence_and_tenant_isolation(monkeypatch):
    """
    Comprova com o modelo real VideoJob + SQLModel (SQLite em memória, sem Neon) que:
    - Configuração explícita persistida na coluna dedicada video_director_mode prevalece sobre env.
    - Configuração é recuperada após commit + expire_all + leitura.
    - Job de outro tenant não altera essa configuração.
    - Conteúdo arbitrário em script_data ou performance_metrics não ativa o Director.
    """
    from sqlmodel import Session, SQLModel, create_engine
    from cloud_hub.models import Channel, Tenant, VideoJob

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    monkeypatch.setenv("VIDEO_DIRECTOR_MODE", "legacy")

    with Session(engine) as session:
        tenant_a = Tenant(name="Tenant A", email="a@example.com")
        tenant_b = Tenant(name="Tenant B", email="b@example.com")
        session.add(tenant_a)
        session.add(tenant_b)
        session.commit()
        session.refresh(tenant_a)
        session.refresh(tenant_b)

        channel_a = Channel(tenant_id=tenant_a.id, name="Chan A", identifier="chan-a", niche="tech", language="en-US", market_region="US")
        channel_b = Channel(tenant_id=tenant_b.id, name="Chan B", identifier="chan-b", niche="finance", language="en-US", market_region="US")
        session.add(channel_a)
        session.add(channel_b)
        session.commit()
        session.refresh(channel_a)
        session.refresh(channel_b)

        job_a = VideoJob(
            channel_id=channel_a.id,
            topic="Persisted Director",
            production_profile="PILOT_5MIN",
            script_data=_make_sample_script_data(topic="Persisted Director"),
            video_director_mode="director",
        )
        job_b = VideoJob(
            channel_id=channel_b.id,
            topic="Other Tenant Arbitrary",
            production_profile="PILOT_5MIN",
            script_data=_make_sample_script_data(
                topic="Other Tenant Arbitrary", mode_override="director"
            ),
        )
        session.add(job_a)
        session.add(job_b)
        session.commit()
        job_a_id = job_a.id
        job_b_id = job_b.id

        # Simula nova leitura limpa do banco
        session.expire_all()
        loaded_a = session.get(VideoJob, job_a_id)
        loaded_b = session.get(VideoJob, job_b_id)

        assert loaded_a is not None
        assert loaded_b is not None

        obs_a = integrate_video_director(
            job=loaded_a,
            tenant_id=tenant_a.id,
            director_factory=lambda: AIVideoDirector(llm_client=_FakeLLMClient()),
            adapter_factory=MasterPlanAdapter,
        )
        obs_b = integrate_video_director(
            job=loaded_b,
            tenant_id=tenant_b.id,
            director_factory=lambda: AIVideoDirector(llm_client=_FakeLLMClient()),
            adapter_factory=MasterPlanAdapter,
        )

        assert obs_a["video_director_mode"] == "director"
        assert obs_a["mode_source"] == "job"
        assert obs_b["video_director_mode"] == "legacy"
        assert obs_b["mode_source"] == "env"


def test_performance_metrics_does_not_activate_director(monkeypatch):
    """
    Garante que job.performance_metrics['video_director_mode'] é ignorado:
    a coluna dedicada job.video_director_mode é a única fonte persistente por job.
    """
    monkeypatch.setenv("VIDEO_DIRECTOR_MODE", "legacy")
    job = SimpleNamespace(
        video_director_mode=None,
        performance_metrics={"video_director_mode": "director"},
        script_data=_make_sample_script_data(mode_override="director"),
    )
    mode, source = resolve_video_director_mode(job=job)
    assert mode == "legacy"
    assert source == "env"


def test_legacy_null_video_director_mode_resolves_to_env_or_default(monkeypatch):
    """
    Jobs antigos com video_director_mode=None devem continuar funcionando,
    resolvendo para VIDEO_DIRECTOR_MODE quando definido ou 'legacy' por default.
    """
    job = SimpleNamespace(video_director_mode=None, script_data={})

    monkeypatch.delenv("VIDEO_DIRECTOR_MODE", raising=False)
    assert resolve_video_director_mode(job=job) == ("legacy", "default")

    monkeypatch.setenv("VIDEO_DIRECTOR_MODE", "director")
    assert resolve_video_director_mode(job=job) == ("director", "env")


def test_independent_sessions_metrics_update_preserves_video_director_mode(tmp_path):
    """
    Teste de sessões independentes (sequencial):
    Session 1 persiste video_director_mode='director'.
    Session 2 (independente) atualiza performance_metrics e faz commit.
    Session 3 (independente) verifica que video_director_mode='director' foi preservado
    sem lost update.
    """
    from sqlmodel import Session, SQLModel, create_engine
    from cloud_hub.models import Channel, Tenant, VideoJob

    db_path = tmp_path / "concurrent_test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    # Sessão 1: cria tenant, canal e job com video_director_mode='director'
    with Session(engine) as session1:
        tenant = Tenant(name="Concurrent Tenant", email="conc@example.com")
        session1.add(tenant)
        session1.commit()
        session1.refresh(tenant)

        channel = Channel(
            tenant_id=tenant.id,
            name="Concurrent Chan",
            identifier="conc-chan",
            niche="tech",
            language="en-US",
            market_region="US",
        )
        session1.add(channel)
        session1.commit()
        session1.refresh(channel)

        job = VideoJob(
            channel_id=channel.id,
            topic="Concurrent Job",
            production_profile="PILOT_5MIN",
            video_director_mode="director",
            script_data=_make_sample_script_data(topic="Concurrent Job"),
        )
        session1.add(job)
        session1.commit()
        job_id = job.id

    # Sessão 2 (independente): atualiza apenas performance_metrics
    with Session(engine) as session2:
        job_s2 = session2.get(VideoJob, job_id)
        assert job_s2 is not None
        job_s2.performance_metrics = {"ctr": 0.15, "views_24h": 1200}
        session2.add(job_s2)
        session2.commit()

    # Sessão 3 (independente): valida que video_director_mode permanece intacto
    with Session(engine) as session3:
        job_s3 = session3.get(VideoJob, job_id)
        assert job_s3 is not None
        assert job_s3.video_director_mode == "director"
        assert job_s3.performance_metrics["ctr"] == 0.15
        assert job_s3.performance_metrics["views_24h"] == 1200
