"""Integration tests for Watchdog State Store with temporary SQLite DB (Sprint 2.7 - Task 3)."""

from __future__ import annotations

from pathlib import Path

from desktop_worker.watchdog.identity import CanonicalExecutionIdentity
from desktop_worker.watchdog.state_store import (
    AttemptHubStatus,
    ExecutionStatus,
    IntegrityStatus,
    StateStore,
    TerminationEvidenceRecord,
)


def test_state_store_full_lifecycle_integration(tmp_path: Path) -> None:
    db_path = tmp_path / "app_data" / "watchdog_state_worker-1.db"
    identity = CanonicalExecutionIdentity(
        execution_id="exec-int-1",
        attempt_id="att-int-1",
        job_id="job-int-1",
        tenant_id="tenant-int",
        channel_id="channel-int",
        worker_id="worker-1",
        fencing_token=100,
        pid=5555,
        process_create_time=133750000005555555,
        job_object_name="Local\\DCF_UPLOAD_att-int-1_exec-int-1",
        browser_user_data_dir="C:\\profiles\\channel-int",
    )

    with StateStore(db_path) as store:
        store.upsert_attempt("att-int-1", "job-int-1", fencing_token=100, hub_status=AttemptHubStatus.IN_PROGRESS)
        store.record_execution(identity, boot_time_utc="2026-11-19T00:00:00Z")
        store.update_heartbeat("att-int-1", expected_fencing_token=100, heartbeat_at_utc="2026-11-19T00:05:00Z")
        store.update_execution_status("exec-int-1", ExecutionStatus.TERMINATING, expected_fencing_token=100)
        store.save_evidence(
            TerminationEvidenceRecord(
                event_id="evt-int-1",
                attempt_id="att-int-1",
                execution_id="exec-int-1",
                job_id="job-int-1",
                worker_id="worker-1",
                fencing_token=100,
                pid=5555,
                process_create_time=133750000005555555,
                job_object_name=identity.job_object_name,
                verification_result="TREE_TERMINATED",
                termination_reason="COMPLETED",
                evidence_source="JOB_OBJECT",
                watchdog_version="2.7.0",
                termination_requested_at_utc="2026-11-19T00:06:00Z",
                verified_at_utc="2026-11-19T00:06:01Z",
                payload_json='{"exit_code":0}',
            )
        )
        store.update_execution_status("exec-int-1", ExecutionStatus.CLOSED, expected_fencing_token=100)

    # Reopen from disk and verify full integrity
    with StateStore(db_path) as reopened:
        exec_rec = reopened.get_execution("exec-int-1")
        assert exec_rec is not None
        assert exec_rec.status == ExecutionStatus.CLOSED
        assert exec_rec.fencing_token == 100

        att_rec = reopened.get_attempt("att-int-1")
        assert att_rec is not None
        assert att_rec.last_heartbeat_at_utc == "2026-11-19T00:05:00Z"

        reports = reopened.verify_integrity()
        assert len(reports) == 4
        assert all(r.status == IntegrityStatus.VALID for r in reports)
