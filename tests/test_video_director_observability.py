import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cloud_hub.core.video_director_integration import integrate_video_director
from cloud_hub.core.video_director_observability import (
    EVENT_COMPLETED,
    EVENT_FAILED,
    EVENT_FALLBACK,
    EVENT_SKIPPED,
    EVENT_STARTED,
    EVENT_TIMEOUT,
    VideoDirectorObservabilityCollector,
    sanitize_text,
)


def _make_job(job_id: int = 101, mode: str | None = "director") -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        video_director_mode=mode,
        production_profile="PILOT_5MIN",
        script_data={
            "title": "Test Video",
            "script_full": "SECRET_NARRATION_CONTENT_DO_NOT_LOG",
            "scenes": [
                {
                    "timestamp": "0:00-0:10",
                    "narration": "SECRET_NARRATION_CONTENT_DO_NOT_LOG",
                    "b_roll_visual": "cinematic city",
                }
            ],
        },
    )


class _DummyDirectorSuccess:
    def create_master_plan(self, script_data, production_profile="PILOT_5MIN"):
        return {"master_plan": "ok", "scenes": script_data["scenes"]}


class _DummyAdapterSuccess:
    def to_pipeline_b(self, master_plan):
        return SimpleNamespace(
            scenes=[
                {
                    "timestamp": "0:00-0:10",
                    "narration": "SECRET_NARRATION_CONTENT_DO_NOT_LOG",
                    "b_roll_visual": "cinematic city",
                }
            ],
            director_metadata={"version": "2.5.0"},
            master_av_plan_summary={"total_scenes": 1},
            preserved_fields_report={"preserved": True},
        )


class _DummyDirectorFailure:
    def create_master_plan(self, script_data, production_profile="PILOT_5MIN"):
        raise RuntimeError("api_key=sk-secret1234567890abcdef failed to connect")


class _DummyDirectorTimeout:
    def create_master_plan(self, script_data, production_profile="PILOT_5MIN"):
        raise TimeoutError("Director timed out after 30s")


def test_events_started_and_completed():
    collector = VideoDirectorObservabilityCollector()
    job = _make_job(job_id=1, mode="director")

    obs = integrate_video_director(
        job=job,
        tenant_id="tenant-a",
        director_factory=_DummyDirectorSuccess,
        adapter_factory=_DummyAdapterSuccess,
        correlation_id="corr-test-1",
        observability_collector=collector,
    )

    events = collector.get_events(tenant_id="tenant-a")
    names = [e["event_name"] for e in events]
    assert names == [EVENT_STARTED, EVENT_COMPLETED]
    assert obs["correlation_id"] == "corr-test-1"
    assert all(e["correlation_id"] == "corr-test-1" for e in events)
    assert all(e["execution_id"] == obs["execution_id"] for e in events)
    assert events[1]["duration_ms"] >= 0.0


def test_events_failed_and_fallback():
    collector = VideoDirectorObservabilityCollector()
    job = _make_job(job_id=2, mode="director")
    original_narration = job.script_data["scenes"][0]["narration"]

    obs = integrate_video_director(
        job=job,
        tenant_id="tenant-a",
        director_factory=_DummyDirectorFailure,
        adapter_factory=_DummyAdapterSuccess,
        correlation_id="corr-fail-1",
        observability_collector=collector,
    )

    assert obs["fallback_triggered"] is True
    assert obs["original_script_restored"] is True
    assert obs["copywriter_reexecuted"] is False
    assert job.script_data["video_director_mode"] == "legacy_fallback"
    assert job.script_data["scenes"][0]["narration"] == original_narration

    events = collector.get_events(tenant_id="tenant-a")
    names = [e["event_name"] for e in events]
    assert names == [EVENT_STARTED, EVENT_FAILED, EVENT_FALLBACK]
    assert events[2]["original_script_restored"] is True
    assert events[2]["copywriter_reexecuted"] is False


def test_events_timeout_and_fallback():
    collector = VideoDirectorObservabilityCollector()
    job = _make_job(job_id=3, mode="director")

    obs = integrate_video_director(
        job=job,
        tenant_id="tenant-b",
        director_factory=_DummyDirectorTimeout,
        adapter_factory=_DummyAdapterSuccess,
        correlation_id="corr-timeout-1",
        observability_collector=collector,
    )

    assert obs["fallback_triggered"] is True
    events = collector.get_events(tenant_id="tenant-b")
    names = [e["event_name"] for e in events]
    assert names == [EVENT_STARTED, EVENT_TIMEOUT, EVENT_FALLBACK]
    assert events[1]["status"] == "timeout"


def test_events_skipped_in_legacy_mode_without_behavior_change():
    collector = VideoDirectorObservabilityCollector()
    job = _make_job(job_id=4, mode="legacy")
    original_data = dict(job.script_data)

    obs = integrate_video_director(
        job=job,
        tenant_id="tenant-legacy",
        correlation_id="corr-legacy-1",
        observability_collector=collector,
    )

    assert obs["video_director_mode"] == "legacy"
    assert job.script_data == original_data
    events = collector.get_events(tenant_id="tenant-legacy")
    assert [e["event_name"] for e in events] == [EVENT_SKIPPED]


def test_sanitization_of_sensitive_data():
    collector = VideoDirectorObservabilityCollector()
    job = _make_job(job_id=5, mode="director")

    integrate_video_director(
        job=job,
        tenant_id="tenant-sec",
        director_factory=_DummyDirectorFailure,
        adapter_factory=_DummyAdapterSuccess,
        observability_collector=collector,
    )

    events = collector.get_events(tenant_id="tenant-sec")
    serialized = json.dumps(events)
    assert "sk-secret1234567890abcdef" not in serialized
    assert "SECRET_NARRATION_CONTENT_DO_NOT_LOG" not in serialized
    assert "[REDACTED]" in serialized

    assert sanitize_text("Bearer eyJhbGciOiJIUzI1NiJ9.abcdef12345.sig") == "Bearer [REDACTED]"


def test_multitenant_isolation():
    collector = VideoDirectorObservabilityCollector()
    job_a = _make_job(job_id=10, mode="legacy")
    job_b = _make_job(job_id=20, mode="legacy")

    integrate_video_director(job=job_a, tenant_id="tenant-1", observability_collector=collector)
    integrate_video_director(job=job_b, tenant_id="tenant-2", observability_collector=collector)

    events_1 = collector.get_events(tenant_id="tenant-1")
    events_2 = collector.get_events(tenant_id="tenant-2")
    assert len(events_1) == 1
    assert len(events_2) == 1
    assert events_1[0]["tenant_id"] == "tenant-1"
    assert events_2[0]["tenant_id"] == "tenant-2"

    with pytest.raises(ValueError):
        collector.get_events(tenant_id="")


def test_observability_failure_does_not_break_pipeline(monkeypatch):
    collector = VideoDirectorObservabilityCollector()

    def _broken_logger(*args, **kwargs):
        raise OSError("Logging subsystem down")

    monkeypatch.setattr("cloud_hub.core.video_director_observability._obs_logger.info", _broken_logger)
    monkeypatch.setattr("cloud_hub.core.video_director_observability._obs_logger.error", _broken_logger)

    job = _make_job(job_id=30, mode="director")
    obs = integrate_video_director(
        job=job,
        tenant_id="tenant-resilient",
        director_factory=_DummyDirectorSuccess,
        adapter_factory=_DummyAdapterSuccess,
        observability_collector=collector,
    )

    assert obs["video_director_mode"] == "director"
    assert obs["adapter_validation_passed"] is True
    assert job.script_data["video_director_mode"] == "director"


def test_metrics_aggregation_and_filtering():
    collector = VideoDirectorObservabilityCollector()

    integrate_video_director(
        job=_make_job(job_id=1, mode="director"),
        tenant_id="t1",
        director_factory=_DummyDirectorSuccess,
        adapter_factory=_DummyAdapterSuccess,
        observability_collector=collector,
    )
    integrate_video_director(
        job=_make_job(job_id=2, mode="director"),
        tenant_id="t2",
        director_factory=_DummyDirectorFailure,
        adapter_factory=_DummyAdapterSuccess,
        observability_collector=collector,
    )
    integrate_video_director(
        job=_make_job(job_id=3, mode="legacy"),
        tenant_id="t3",
        observability_collector=collector,
    )

    metrics = collector.get_metrics()
    assert metrics["total_executions"] == 3
    assert metrics["completed_executions"] == 1
    assert metrics["failed_executions"] == 1
    assert metrics["skipped_executions"] == 1
    assert metrics["fallback_count"] == 1
    assert metrics["fallback_rate"] == round(1 / 3, 4)
    assert "director" in metrics["duration_by_mode"]
    assert "legacy" in metrics["duration_by_mode"]
    assert "RuntimeError" in metrics["failures_by_error_code"]
    # Sem cardinalidade excessiva
    assert "tenant_id" not in metrics
    assert "job_id" not in metrics

    # Filtro por modo e período
    director_only = collector.get_metrics(
        mode="director",
        since_utc=datetime.now(timezone.utc) - timedelta(minutes=5),
        until_utc=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    assert director_only["total_executions"] == 2
