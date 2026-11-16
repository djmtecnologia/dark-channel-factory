"""
Testes de persistência da feature flag `video_director_mode` (Sprint 2.5 - Tarefa 2).

Valida:
1. Ciclo operacional completo com o modelo real `VideoJob` em SQLite isolado.
2. Preservação da configuração após atualizações completas ou parciais de `performance_metrics`.
3. Isolamento entre jobs/tenants.
4. Atualização simultânea de métricas e configuração sem perda de atualização.
5. Origem confiável da configuração (`create_video_job` / coluna própria) e bloqueio de `script_data`.
"""

from __future__ import annotations

import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, SQLModel, create_engine

from cloud_hub.core.video_director_integration import (
    integrate_video_director,
    resolve_video_director_mode,
)
from cloud_hub.main import JobCreateRequest, create_video_job
from cloud_hub.models import Channel, Tenant, VideoJob


@pytest.fixture()
def sqlite_engine():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_tenant_and_channel(session: Session, name: str = "Tenant A") -> tuple[Tenant, Channel]:
    tenant = Tenant(
        name=name,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        api_key=f"key-{uuid.uuid4().hex}",
    )
    session.add(tenant)
    session.commit()
    session.refresh(tenant)

    channel = Channel(
        tenant_id=tenant.id,
        name=f"Channel {name}",
        youtube_channel_id=f"UC-{uuid.uuid4().hex[:10]}",
        niche="AI + Money + Stories",
        language="en-US",
        identifier=f"en-US-{uuid.uuid4().hex[:6]}",
        market_region="US",
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return tenant, channel


def test_operational_cycle_preserves_director_mode_across_metrics_updates(sqlite_engine, monkeypatch):
    monkeypatch.delenv("VIDEO_DIRECTOR_MODE", raising=False)

    # 1. Criar job com modo 'director' e outro job independente (legacy/default)
    with Session(sqlite_engine) as session:
        tenant_a, channel_a = _seed_tenant_and_channel(session, "Tenant A")
        tenant_b, channel_b = _seed_tenant_and_channel(session, "Tenant B")

        job_director = VideoJob(
            channel_id=channel_a.id,
            topic="AI Video Director Persistence Test",
            video_director_mode="director",
        )
        job_other = VideoJob(
            channel_id=channel_b.id,
            topic="Other Tenant Legacy Job",
        )
        session.add(job_director)
        session.add(job_other)
        session.commit()
        session.refresh(job_director)
        session.refresh(job_other)
        director_id = job_director.id
        other_id = job_other.id

    # 2. Recuperar em nova sessão e atualizar performance_metrics (substituição completa do JSON)
    with Session(sqlite_engine) as session:
        reloaded = session.get(VideoJob, director_id)
        assert reloaded is not None
        assert reloaded.video_director_mode == "director"

        reloaded.performance_metrics = {
            "ctr": 8.4,
            "hook_retention": 72.5,
            "average_view_duration": 185.0,
            "views_24h": 12400,
        }
        session.add(reloaded)
        session.commit()

    # 3. Recuperar novamente em nova sessão e atualizar métricas parcialmente
    with Session(sqlite_engine) as session:
        reloaded_again = session.get(VideoJob, director_id)
        assert reloaded_again is not None
        assert reloaded_again.video_director_mode == "director"
        assert reloaded_again.performance_metrics["views_24h"] == 12400

        reloaded_again.performance_metrics["views_24h"] = 25000
        flag_modified(reloaded_again, "performance_metrics")
        session.add(reloaded_again)
        session.commit()

    # 4. Confirmação final em nova sessão + isolamento do outro job/tenant
    with Session(sqlite_engine) as session:
        final_director = session.get(VideoJob, director_id)
        final_other = session.get(VideoJob, other_id)

        assert final_director is not None
        assert final_director.video_director_mode == "director"
        assert final_director.performance_metrics["views_24h"] == 25000
        assert resolve_video_director_mode(final_director) == ("director", "job")

        assert final_other is not None
        assert final_other.video_director_mode is None
        assert resolve_video_director_mode(final_other) == ("legacy", "default")


def test_simultaneous_metrics_and_mode_update_no_lost_update(sqlite_engine, monkeypatch):
    monkeypatch.delenv("VIDEO_DIRECTOR_MODE", raising=False)

    with Session(sqlite_engine) as session:
        _, channel = _seed_tenant_and_channel(session, "Tenant Simultaneous")
        job = VideoJob(
            channel_id=channel.id,
            topic="Simultaneous Update",
            video_director_mode="legacy",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    with Session(sqlite_engine) as session:
        job = session.get(VideoJob, job_id)
        assert job is not None
        job.video_director_mode = "director"
        job.performance_metrics = {"ctr": 9.1, "views_24h": 999}
        session.add(job)
        session.commit()

    with Session(sqlite_engine) as session:
        reloaded = session.get(VideoJob, job_id)
        assert reloaded is not None
        assert reloaded.video_director_mode == "director"
        assert reloaded.performance_metrics == {"ctr": 9.1, "views_24h": 999}
        assert resolve_video_director_mode(reloaded) == ("director", "job")


def test_script_data_cannot_activate_director(sqlite_engine, monkeypatch):
    monkeypatch.delenv("VIDEO_DIRECTOR_MODE", raising=False)

    with Session(sqlite_engine) as session:
        _, channel = _seed_tenant_and_channel(session, "Tenant Security")
        job = VideoJob(
            channel_id=channel.id,
            topic="Untrusted script_data",
            script_data={
                "title": "Test",
                "video_director_mode": "director",
                "scenes": [{"narration": "Hello"}],
            },
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        assert resolve_video_director_mode(job) == ("legacy", "default")
        obs = integrate_video_director(job=job, tenant_id=channel.tenant_id)
        assert obs["video_director_mode"] == "legacy"
        assert obs["fallback_triggered"] is False


def test_create_video_job_persists_and_validates_director_mode(sqlite_engine, monkeypatch):
    monkeypatch.delenv("VIDEO_DIRECTOR_MODE", raising=False)

    with Session(sqlite_engine) as session:
        tenant, channel = _seed_tenant_and_channel(session, "Tenant API")

        req = JobCreateRequest(
            channel_id=channel.id,
            topic="API Created Director Job",
            video_director_mode="director",
        )
        resp = create_video_job(payload=req, tenant=tenant, session=session)
        job_id = resp["job_id"]
        channel_id = channel.id
        tenant_id = tenant.id

    with Session(sqlite_engine) as session:
        persisted = session.get(VideoJob, job_id)
        assert persisted is not None
        assert persisted.video_director_mode == "director"
        assert resolve_video_director_mode(persisted) == ("director", "job")

        tenant_reloaded = session.get(Tenant, tenant_id)
        bad_req = JobCreateRequest(
            channel_id=channel_id,
            topic="Invalid Mode",
            video_director_mode="turbo_mode",
        )
        with pytest.raises(HTTPException) as exc_info:
            create_video_job(payload=bad_req, tenant=tenant_reloaded, session=session)
        assert exc_info.value.status_code == 400
