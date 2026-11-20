"""Integration tests for WatchdogDaemon + SafeTerminationEngine + StateStore + OutboxStore."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from desktop_worker.watchdog.identity import (
    CanonicalExecutionIdentity,
    IdentityStatus,
    IdentityVerificationResult,
)
from desktop_worker.watchdog.job_object import WAIT_OBJECT_0, BasicAccountingInfo
from desktop_worker.watchdog.outbox import OutboxEventStatus, OutboxStore
from desktop_worker.watchdog.state_store import (
    AttemptHubStatus,
    ExecutionStatus,
    StateStore,
)
from desktop_worker.watchdog.termination_engine import (
    OrphanClassification,
    SafeTerminationEngine,
    TerminationContext,
)
from desktop_worker.watchdog.termination_evidence import (
    TerminationOutboxEventType,
    TerminationVerificationResult,
)
from desktop_worker.watchdog.watchdog_daemon import (
    WatchdogConfig,
    WatchdogDaemon,
    WatchdogDaemonError,
)


class _StubWin32:
    def __init__(self) -> None:
        self.terminated = False

    def terminate_job_object(self, job_handle: int, exit_code: int) -> bool:
        self.terminated = True
        return True

    def get_last_error(self) -> int:
        return 0

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        return WAIT_OBJECT_0

    def get_exit_code_process(self, handle: int) -> tuple[bool, int]:
        return True, 1

    def query_basic_accounting(self, job_handle: int):
        class _Acct:
            ActiveProcesses = 0 if self.terminated else 1
            TotalProcesses = 1
            TotalTerminatedProcesses = 1 if self.terminated else 0

        return True, _Acct(), 0

    def query_process_id_list(self, job_handle: int, capacity: int = 64):
        class _Pids:
            NumberOfAssignedProcesses = 0 if self.terminated else 1
            NumberOfProcessIdsInList = 0 if self.terminated else 1
            ProcessIdList = [] if self.terminated else [7777]

        return True, _Pids(), 0


class _StubValidator:
    def __init__(self, status: IdentityStatus = IdentityStatus.VALID) -> None:
        self.status = status

    def verify(self, expected, *, job_handle=None, tolerance_ticks=0):
        return IdentityVerificationResult(
            status=self.status,
            pid=expected.pid,
            expected_create_time=expected.process_create_time,
            actual_create_time=expected.process_create_time,
            in_expected_job=(self.status == IdentityStatus.VALID),
            reason=f"Stubbed {self.status.value}",
        )


def test_watchdog_daemon_delegates_safe_termination_and_preserves_integrity(tmp_path: Path) -> None:
    db_path = tmp_path / "daemon_term.db"
    worker_id = f"worker-int-{uuid.uuid4().hex[:8]}"
    store = StateStore(db_path)
    outbox = OutboxStore(store)

    ident = CanonicalExecutionIdentity(
        execution_id="exec-int-1",
        attempt_id="att-int-1",
        job_id="job-int-1",
        tenant_id="tenant-1",
        channel_id="chan-1",
        worker_id=worker_id,
        fencing_token=42,
        pid=7777,
        process_create_time=133000000000000000,
        job_object_name="dcf-job-int-1",
        browser_user_data_dir="C:/tmp/prof-int-1",
    )
    store.upsert_attempt(
        attempt_id=ident.attempt_id,
        job_id=ident.job_id,
        fencing_token=ident.fencing_token,
        hub_status=AttemptHubStatus.IN_PROGRESS,
        last_heartbeat_at_utc=(datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(),
    )
    store.record_execution(ident, boot_time_utc="2026-11-19T10:00:00+00:00", status=ExecutionStatus.RUNNING)

    engine = SafeTerminationEngine(
        store,
        outbox_store=outbox,
        win32=_StubWin32(),
        identity_validator=_StubValidator(IdentityStatus.VALID),
    )
    engine.register_job_handle(ident.execution_id, 1234)

    cfg = WatchdogConfig(
        worker_id=worker_id,
        db_path=db_path,
        poll_interval_sec=1.0,
        heartbeat_interval_sec=5.0,
        heartbeat_timeout_sec=30.0,
    )
    import os
    from desktop_worker.watchdog.job_handle_transfer import Win32HandleTransferApi

    launcher_ct = Win32HandleTransferApi().get_current_process_create_time()
    daemon = WatchdogDaemon(
        cfg,
        termination_engine=engine,
        expected_launcher_pid=os.getpid(),
        expected_launcher_create_time=launcher_ct,
    )

    # Without mutex ownership, request_safe_termination must be rejected
    with pytest.raises(WatchdogDaemonError, match="Mutex ownership"):
        daemon.request_safe_termination(
            TerminationContext(identity=ident, job_handle=1234, process_handle=5678, event_id="evt-int-1")
        )

    daemon.start()
    try:
        outcome = daemon.request_safe_termination(
            TerminationContext(identity=ident, job_handle=1234, process_handle=5678, event_id="evt-int-1")
        )
        assert outcome.verification_result == TerminationVerificationResult.TREE_TERMINATED
        assert outcome.fully_completed is True
        assert outcome.evidence_persisted is True
        assert outcome.outbox_enqueued is True

        # State Store and Outbox integrity remain completely valid
        reports = store.verify_integrity()
        assert all(r.is_valid for r in reports)
        ev = outbox.get_event("evt-int-1")
        assert ev is not None
        assert ev.event_type == TerminationOutboxEventType.TERMINATION_CONFIRMED.value
        assert ev.status == OutboxEventStatus.PENDING
    finally:
        assert daemon.stop() is True
        store.close()


def test_recovery_after_inconclusive_termination(tmp_path: Path) -> None:
    db_path = tmp_path / "recovery.db"
    store = StateStore(db_path)
    outbox = OutboxStore(store)
    ident = CanonicalExecutionIdentity(
        execution_id="exec-rec-1",
        attempt_id="att-rec-1",
        job_id="job-rec-1",
        tenant_id="tenant-1",
        channel_id="chan-1",
        worker_id="worker-rec",
        fencing_token=7,
        pid=8888,
        process_create_time=133000000000000000,
        job_object_name="dcf-job-rec-1",
        browser_user_data_dir="C:/tmp/prof-rec-1",
    )
    store.upsert_attempt(
        attempt_id=ident.attempt_id,
        job_id=ident.job_id,
        fencing_token=ident.fencing_token,
        hub_status=AttemptHubStatus.IN_PROGRESS,
        last_heartbeat_at_utc="2026-11-19T10:00:00+00:00",
    )
    store.record_execution(ident, boot_time_utc="2026-11-19T10:00:00+00:00", status=ExecutionStatus.RUNNING)

    # First attempt: UNVERIFIABLE -> INCONCLUSIVE persisted, state preserved
    engine_unverifiable = SafeTerminationEngine(
        store,
        outbox_store=outbox,
        win32=_StubWin32(),
        identity_validator=_StubValidator(IdentityStatus.UNVERIFIABLE),
    )
    engine_unverifiable.register_job_handle(ident.execution_id, 1234)
    first = engine_unverifiable.terminate_execution(
        TerminationContext(identity=ident, job_handle=1234, event_id="evt-rec-inconclusive")
    )
    assert first.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert first.evidence_persisted is True
    assert store.get_evidence("evt-rec-inconclusive") is not None

    # Subsequent attempt with VALID identity succeeds without overwriting prior evidence
    engine_valid = SafeTerminationEngine(
        store,
        outbox_store=outbox,
        win32=_StubWin32(),
        identity_validator=_StubValidator(IdentityStatus.VALID),
    )
    engine_valid.register_job_handle(ident.execution_id, 1234)
    second = engine_valid.terminate_execution(
        TerminationContext(identity=ident, job_handle=1234, process_handle=5678, event_id="evt-rec-confirmed")
    )
    assert second.verification_result == TerminationVerificationResult.TREE_TERMINATED
    assert second.fully_completed is True
    assert store.get_evidence("evt-rec-inconclusive") is not None
    assert store.get_evidence("evt-rec-confirmed") is not None
    assert outbox.get_event("evt-rec-inconclusive") is not None
    assert outbox.get_event("evt-rec-confirmed") is not None
    store.close()
