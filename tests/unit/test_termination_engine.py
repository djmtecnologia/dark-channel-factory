"""Unit tests for Safe Process Termination Engine & Termination Evidence (Sprint 2.7 - Task 6)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from desktop_worker.watchdog.identity import (
    CanonicalExecutionIdentity,
    IdentityStatus,
    IdentityVerificationResult,
)
from desktop_worker.watchdog.job_object import (
    ERROR_MORE_DATA,
    WAIT_FAILED,
    WAIT_OBJECT_0,
    WAIT_TIMEOUT,
    BasicAccountingInfo,
)
from desktop_worker.watchdog.outbox import OutboxEventStatus, OutboxStore
from desktop_worker.watchdog.state_store import (
    AttemptHubStatus,
    CheckpointRecord,
    ExecutionStatus,
    StateStore,
    StateStorePersistenceError,
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


# ---------------------------------------------------------------------------
# Helpers & Fakes
# ---------------------------------------------------------------------------


def _make_identity(
    *,
    execution_id: str = "exec-1",
    attempt_id: str = "att-1",
    job_id: str = "job-1",
    worker_id: str = "worker-1",
    fencing_token: int = 10,
    pid: int = 4321,
    process_create_time: int = 133000000000000000,
    job_object_name: str = "dcf-job-exec-1",
) -> CanonicalExecutionIdentity:
    return CanonicalExecutionIdentity(
        execution_id=execution_id,
        attempt_id=attempt_id,
        job_id=job_id,
        tenant_id="tenant-1",
        channel_id="chan-1",
        worker_id=worker_id,
        fencing_token=fencing_token,
        pid=pid,
        process_create_time=process_create_time,
        job_object_name=job_object_name,
        browser_user_data_dir="C:/tmp/profile-1",
    )


def _seed_store(
    tmp_path: Path,
    identity: CanonicalExecutionIdentity,
    *,
    boot_time_utc: str = "2026-11-19T12:00:00+00:00",
    heartbeat_at_utc: Optional[str] = "2026-11-19T12:00:00+00:00",
    status: ExecutionStatus = ExecutionStatus.RUNNING,
) -> Tuple[StateStore, OutboxStore]:
    store = StateStore(tmp_path / "watchdog.db")
    outbox = OutboxStore(store)
    store.upsert_attempt(
        attempt_id=identity.attempt_id,
        job_id=identity.job_id,
        fencing_token=identity.fencing_token,
        hub_status=AttemptHubStatus.IN_PROGRESS,
        last_heartbeat_at_utc=heartbeat_at_utc,
    )
    store.record_execution(
        identity,
        boot_time_utc=boot_time_utc,
        status=status,
    )
    return store, outbox


class FakeIdentityValidator:
    def __init__(self, results: List[IdentityVerificationResult]) -> None:
        self._results = list(results)
        self.calls: List[Tuple[Any, Optional[int]]] = []

    def verify(
        self,
        expected: Any,
        *,
        job_handle: Optional[int] = None,
        tolerance_ticks: int = 0,
    ) -> IdentityVerificationResult:
        self.calls.append((expected, job_handle))
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0]


class _FakeAcctStruct:
    def __init__(self, active: int, total: int, terminated: int) -> None:
        self.ActiveProcesses = active
        self.TotalProcesses = total
        self.TotalTerminatedProcesses = terminated


class _FakePidListStruct:
    def __init__(self, pids: List[int], assigned: int, returned: int) -> None:
        self.NumberOfAssignedProcesses = assigned
        self.NumberOfProcessIdsInList = returned
        self.ProcessIdList = pids


class FakeWin32Api:
    def __init__(self) -> None:
        self.terminate_calls: List[Tuple[int, int]] = []
        self.terminate_return: bool = True
        self.terminate_raises: Optional[Exception] = None
        self.last_error: int = 5
        self.wait_results: List[int] = [WAIT_OBJECT_0]
        self.wait_raises: Optional[Exception] = None
        self.exit_code: Optional[int] = 1
        self.accounting_sequence: List[Optional[BasicAccountingInfo]] = [
            BasicAccountingInfo(active_processes=1, total_processes=1, total_terminated_processes=0),
            BasicAccountingInfo(active_processes=0, total_processes=1, total_terminated_processes=1),
        ]
        self.pid_list_sequence: List[Tuple[bool, List[int], int, int, Optional[int]]] = [
            (True, [4321], 1, 1, None),
            (True, [], 0, 0, None),
        ]
        self.terminate_delay_sec: float = 0.0

    def terminate_job_object(self, job_handle: int, exit_code: int) -> bool:
        self.terminate_calls.append((job_handle, exit_code))
        if self.terminate_delay_sec > 0:
            time.sleep(self.terminate_delay_sec)
        if self.terminate_raises is not None:
            raise self.terminate_raises
        return self.terminate_return

    def get_last_error(self) -> int:
        return self.last_error

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        if self.wait_raises is not None:
            raise self.wait_raises
        if len(self.wait_results) > 1:
            return self.wait_results.pop(0)
        return self.wait_results[0]

    def get_exit_code_process(self, handle: int) -> Tuple[bool, int]:
        if self.exit_code is None:
            return False, 0
        return True, self.exit_code

    def query_basic_accounting(self, job_handle: int) -> Tuple[bool, Optional[_FakeAcctStruct], int]:
        item = (
            self.accounting_sequence.pop(0)
            if len(self.accounting_sequence) > 1
            else self.accounting_sequence[0]
        )
        if item is None:
            return False, None, 5
        return (
            True,
            _FakeAcctStruct(
                item.active_processes,
                item.total_processes,
                item.total_terminated_processes,
            ),
            0,
        )

    def query_process_id_list(
        self, job_handle: int, capacity: int = 64
    ) -> Tuple[bool, Optional[_FakePidListStruct], int]:
        ok, pids, assigned, returned, err = (
            self.pid_list_sequence.pop(0)
            if len(self.pid_list_sequence) > 1
            else self.pid_list_sequence[0]
        )
        if not ok and err != ERROR_MORE_DATA:
            return False, None, err or 5
        return ok, _FakePidListStruct(pids, assigned, returned), err or 0


# ---------------------------------------------------------------------------
# Identity Validation & Blocking Tests
# ---------------------------------------------------------------------------


def test_valid_identity_terminates_and_persists_evidence_and_outbox(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [
            IdentityVerificationResult(
                status=IdentityStatus.VALID,
                pid=ident.pid,
                expected_create_time=ident.process_create_time,
                actual_create_time=ident.process_create_time,
                in_expected_job=True,
                reason="Process identity verified",
            )
        ]
    )
    engine = SafeTerminationEngine(
        store,
        outbox_store=outbox,
        win32=win32,
        identity_validator=validator,
    )
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(
            identity=ident,
            job_handle=9001,
            process_handle=9002,
            event_id="evt-term-valid-1",
        )
    )

    assert outcome.verification_result == TerminationVerificationResult.TREE_TERMINATED
    assert outcome.fully_completed is True
    assert outcome.evidence_persisted is True
    assert outcome.outbox_enqueued is True
    assert outcome.active_processes_before == 1
    assert outcome.active_processes_after == 0
    assert outcome.pids_before == [4321]
    assert outcome.pids_after == []
    assert outcome.wait_result == WAIT_OBJECT_0
    assert outcome.exit_code == 1
    assert win32.terminate_calls == [(9001, 1)]
    assert len(validator.calls) == 2  # initial check + pre-termination re-check

    ev = store.get_evidence("evt-term-valid-1")
    assert ev is not None
    assert ev.event_id == "evt-term-valid-1"
    assert ev.verification_result == "TREE_TERMINATED"

    outbox_ev = outbox.get_event("evt-term-valid-1")
    assert outbox_ev is not None
    assert outbox_ev.event_type == TerminationOutboxEventType.TERMINATION_CONFIRMED.value
    assert outbox_ev.status == OutboxEventStatus.PENDING
    store.close()


@pytest.mark.parametrize(
    "status,expected_class,expected_err",
    [
        (IdentityStatus.PID_REUSED, OrphanClassification.IDENTITY_MISMATCH, "PID_REUSED"),
        (IdentityStatus.NOT_IN_JOB, OrphanClassification.IDENTITY_MISMATCH, "NOT_IN_JOB"),
        (IdentityStatus.UNVERIFIABLE, OrphanClassification.INCONCLUSIVE, "IDENTITY_UNVERIFIABLE"),
    ],
)
def test_non_valid_identity_blocks_termination(
    tmp_path: Path,
    status: IdentityStatus,
    expected_class: OrphanClassification,
    expected_err: str,
) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [
            IdentityVerificationResult(
                status=status,
                pid=ident.pid,
                expected_create_time=ident.process_create_time,
                reason=f"Simulated {status.value}",
            )
        ]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id=f"evt-{status.value}")
    )

    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.classification == expected_class
    assert outcome.error_code == expected_err
    assert outcome.fully_completed is False
    assert outcome.evidence_persisted is True
    assert outcome.outbox_enqueued is True
    assert win32.terminate_calls == []
    store.close()


def test_process_not_found_confirms_tree_terminated_when_job_empty(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    win32.accounting_sequence = [
        BasicAccountingInfo(active_processes=0, total_processes=1, total_terminated_processes=1)
    ]
    win32.pid_list_sequence = [(True, [], 0, 0, None)]
    validator = FakeIdentityValidator(
        [
            IdentityVerificationResult(
                status=IdentityStatus.PROCESS_NOT_FOUND,
                pid=ident.pid,
                expected_create_time=ident.process_create_time,
                reason="Process exited",
            )
        ]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-already-gone")
    )

    assert outcome.verification_result == TerminationVerificationResult.TREE_TERMINATED
    assert outcome.classification == OrphanClassification.ALREADY_TERMINATED
    assert outcome.fully_completed is True
    assert win32.terminate_calls == []
    store.close()


def test_fencing_token_mismatch_blocks_termination_without_mutating_state(tmp_path: Path) -> None:
    ident = _make_identity(fencing_token=10)
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)

    stale_ident = _make_identity(fencing_token=9)
    outcome = engine.terminate_execution(
        TerminationContext(identity=stale_ident, job_handle=9001, expected_fencing_token=9)
    )

    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.classification == OrphanClassification.IDENTITY_MISMATCH
    assert outcome.error_code == "FENCING_TOKEN_MISMATCH"
    assert outcome.fully_completed is False
    assert win32.terminate_calls == []
    assert validator.calls == []
    assert store.get_execution(ident.execution_id).fencing_token == 10
    store.close()


def test_missing_job_handle_blocks_termination(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator([])
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=None, event_id="evt-no-handle")
    )

    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.error_code == "MISSING_JOB_HANDLE"
    assert outcome.evidence_persisted is True
    assert win32.terminate_calls == []
    store.close()


# ---------------------------------------------------------------------------
# Failure, Timeout, Truncation & Interruption Tests
# ---------------------------------------------------------------------------


def test_terminate_job_object_failure_returns_termination_failed(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    win32.terminate_return = False
    win32.last_error = 5
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-term-fail")
    )

    assert outcome.verification_result == TerminationVerificationResult.TERMINATION_FAILED
    assert outcome.error_code == "TERMINATE_JOB_OBJECT_FAILED"
    assert outcome.fully_completed is False
    assert outcome.evidence_persisted is True
    outbox_ev = outbox.get_event("evt-term-fail")
    assert outbox_ev is not None
    assert outbox_ev.event_type == TerminationOutboxEventType.TERMINATION_FAILED.value
    store.close()


def test_wait_for_single_object_failed_returns_inconclusive(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    win32.wait_results = [WAIT_FAILED]
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, process_handle=9002, event_id="evt-wait-fail")
    )

    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.error_code == "WAIT_FOR_SINGLE_OBJECT_FAILED"
    assert outcome.wait_result == WAIT_FAILED
    assert outcome.fully_completed is False
    store.close()


def test_accounting_query_failure_returns_inconclusive(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    win32.accounting_sequence = [
        BasicAccountingInfo(active_processes=1, total_processes=1, total_terminated_processes=0),
        None,
    ]
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-acct-fail")
    )

    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.error_code == "ACCOUNTING_QUERY_FAILED"
    assert outcome.fully_completed is False
    store.close()


def test_truncated_pid_list_never_classified_as_tree_terminated(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    win32.accounting_sequence = [
        BasicAccountingInfo(active_processes=1, total_processes=1, total_terminated_processes=0),
        BasicAccountingInfo(active_processes=0, total_processes=1, total_terminated_processes=1),
    ]
    # Simulate persistent ERROR_MORE_DATA truncation across buffer retries
    win32.pid_list_sequence = [
        (True, [4321], 1, 1, None),
        (False, [4321], 5, 1, ERROR_MORE_DATA),
    ]
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-trunc")
    )

    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.pids_truncated is True
    assert outcome.error_code == "PID_LIST_TRUNCATED"
    assert outcome.fully_completed is False
    store.close()


def test_timeout_returns_termination_timeout_not_tree_terminated(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    win32.wait_results = [WAIT_TIMEOUT]
    win32.accounting_sequence = [
        BasicAccountingInfo(active_processes=1, total_processes=1, total_terminated_processes=0),
    ]
    win32.pid_list_sequence = [(True, [4321], 1, 1, None)]
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(
        store,
        outbox_store=outbox,
        win32=win32,
        identity_validator=validator,
        poll_interval_ms=10,
    )
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, process_handle=9002, event_id="evt-timeout"),
        timeout_ms=40,
    )

    assert outcome.verification_result == TerminationVerificationResult.TERMINATION_TIMEOUT
    assert outcome.error_code == "TERMINATION_TIMEOUT"
    assert outcome.fully_completed is False
    assert outcome.evidence_persisted is True
    store.close()


def test_stop_event_interruption_returns_inconclusive(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    stop_event = threading.Event()
    stop_event.set()
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(
        store,
        outbox_store=outbox,
        win32=win32,
        identity_validator=validator,
        stop_event=stop_event,
    )
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-interrupted")
    )

    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.error_code == "DAEMON_INTERRUPTED"
    assert outcome.fully_completed is False
    assert win32.terminate_calls == []
    store.close()


# ---------------------------------------------------------------------------
# Persistence, Idempotency, Transaction Rollback & Concurrency Tests
# ---------------------------------------------------------------------------


def test_persistence_failure_prevents_fully_completed(tmp_path: Path) -> None:
    ident = _make_identity()
    store, _ = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )

    mock_store = MagicMock()
    mock_store.get_execution.return_value = store.get_execution(ident.execution_id)
    mock_store.get_attempt.return_value = store.get_attempt(ident.attempt_id)
    mock_store.get_process_identities.return_value = store.get_process_identities(ident.execution_id)
    mock_store.get_evidence_for_execution.return_value = []
    mock_store.save_evidence.side_effect = StateStorePersistenceError("disk full")

    engine = SafeTerminationEngine(mock_store, outbox_store=None, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)
    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-persist-fail")
    )

    assert outcome.verification_result == TerminationVerificationResult.TREE_TERMINATED
    assert outcome.evidence_persisted is False
    assert outcome.fully_completed is False
    assert outcome.error_code == "EVIDENCE_PERSISTENCE_FAILED"
    store.close()


def test_duplicate_event_id_idempotent_and_conflict(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)

    first = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-dup-1")
    )
    assert first.evidence_persisted is True

    # Re-persisting the exact same record via recorder is idempotent
    dup_res = engine._recorder.persist(first.evidence_record)
    assert dup_res.persisted is True
    assert dup_res.idempotent_duplicate is True

    # Re-persisting same event_id with different content fails safely
    conflicting = first.evidence_record.__class__(
        **{**first.evidence_record.__dict__, "termination_reason": "DIFFERENT_REASON"}
    )
    conflict_res = engine._recorder.persist(conflicting)
    assert conflict_res.persisted is False
    assert "conflicting content" in (conflict_res.error or "").lower()
    store.close()


def test_atomic_transaction_rollback_when_outbox_fails(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )

    original_enqueue = outbox.enqueue_event

    def _failing_enqueue(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("simulated outbox write failure")

    outbox.enqueue_event = _failing_enqueue  # type: ignore[method-assign]
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-tx-rollback")
    )
    outbox.enqueue_event = original_enqueue  # type: ignore[method-assign]

    assert outcome.evidence_persisted is False
    assert outcome.outbox_enqueued is False
    assert outcome.fully_completed is False
    # Verify rollback: neither evidence nor outbox event was committed
    assert store.get_evidence("evt-tx-rollback") is None
    assert outbox.get_event("evt-tx-rollback") is None
    store.close()


def test_concurrent_termination_blocked(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    win32.terminate_delay_sec = 0.2
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)

    results: List[Any] = []
    t1 = threading.Thread(
        target=lambda: results.append(
            engine.terminate_execution(
                TerminationContext(identity=ident, job_handle=9001, event_id="evt-conc-1")
            )
        )
    )
    t1.start()
    time.sleep(0.05)
    second = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-conc-2")
    )
    t1.join(timeout=5.0)

    assert second.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert second.error_code == "CONCURRENT_TERMINATION_IN_PROGRESS"
    assert len(results) == 1
    assert results[0].verification_result == TerminationVerificationResult.TREE_TERMINATED
    store.close()


# ---------------------------------------------------------------------------
# Orphan Classification Tests (Section 3.1)
# ---------------------------------------------------------------------------


def test_orphan_classification_rules(tmp_path: Path) -> None:
    now = datetime(2026, 11, 19, 12, 10, 0, tzinfo=timezone.utc)
    ident = _make_identity()
    store, outbox = _seed_store(
        tmp_path,
        ident,
        boot_time_utc="2026-11-19T10:00:00+00:00",
        heartbeat_at_utc=(now - timedelta(seconds=120)).isoformat(),
    )
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(
        store,
        outbox_store=outbox,
        win32=FakeWin32Api(),
        identity_validator=validator,
        clock=lambda: now,
    )

    # 1. Heartbeat expired alone (without job_handle) -> ORPHAN_CANDIDATE, never SAFE_TO_TERMINATE
    res_candidate = engine.classify_orphan(
        ident.execution_id,
        heartbeat_timeout_sec=30.0,
        job_handle=None,
        current_boot_time_utc="2026-11-19T10:00:00+00:00",
    )
    assert res_candidate.classification == OrphanClassification.ORPHAN_CANDIDATE
    assert res_candidate.heartbeat_expired is True

    # 2. Different boot -> INCONCLUSIVE
    res_boot = engine.classify_orphan(
        ident.execution_id,
        heartbeat_timeout_sec=30.0,
        job_handle=9001,
        current_boot_time_utc="2026-11-19T11:59:00+00:00",
    )
    assert res_boot.classification == OrphanClassification.INCONCLUSIVE
    assert res_boot.different_boot is True

    # 3. Heartbeat expired + same boot + VALID identity -> SAFE_TO_TERMINATE
    res_safe = engine.classify_orphan(
        ident.execution_id,
        heartbeat_timeout_sec=30.0,
        job_handle=9001,
        current_boot_time_utc="2026-11-19T10:00:00+00:00",
    )
    assert res_safe.classification == OrphanClassification.SAFE_TO_TERMINATE

    # 4. Healthy heartbeat -> NOT_ORPHANED
    store.update_heartbeat(
        ident.attempt_id,
        expected_fencing_token=ident.fencing_token,
        heartbeat_at_utc=(now - timedelta(seconds=5)).isoformat(),
    )
    res_healthy = engine.classify_orphan(
        ident.execution_id,
        heartbeat_timeout_sec=30.0,
        job_handle=9001,
        current_boot_time_utc="2026-11-19T10:00:00+00:00",
    )
    assert res_healthy.classification == OrphanClassification.NOT_ORPHANED
    store.close()


# ---------------------------------------------------------------------------
# Persisted Identity Authorization Tests (Corrective Round - Sections 1-5)
# ---------------------------------------------------------------------------


def _assert_precheck_blocked(
    outcome: Any,
    win32: FakeWin32Api,
    db_path: Path,
    execution_id: str,
    *,
    expected_classifications: Tuple[OrphanClassification, ...] = (
        OrphanClassification.IDENTITY_MISMATCH,
        OrphanClassification.INCONCLUSIVE,
    ),
) -> None:
    assert win32.terminate_calls == []
    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.classification in expected_classifications
    assert outcome.reason.strip() != ""
    assert outcome.fully_completed is False
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT verification_result FROM termination_evidence WHERE execution_id = ?;",
            (execution_id,),
        ).fetchall()
    assert not any(row[0] == "TREE_TERMINATED" for row in rows)


@pytest.mark.parametrize(
    "field_name,divergent_value",
    [
        ("pid", 99999),
        ("process_create_time", 133999999999999999),
        ("attempt_id", "att-divergent"),
        ("job_id", "job-divergent"),
        ("tenant_id", "tenant-divergent"),
        ("channel_id", "chan-divergent"),
        ("worker_id", "worker-divergent"),
        ("job_object_name", "dcf-job-divergent"),
        ("browser_user_data_dir", "C:/tmp/profile-divergent"),
    ],
)
def test_persisted_identity_field_divergence_blocks_termination(
    tmp_path: Path,
    field_name: str,
    divergent_value: Any,
) -> None:
    """Every mandatory identity field mismatch against persisted records must block TerminateJobObject."""
    persisted = _make_identity()
    store, outbox = _seed_store(tmp_path, persisted)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [
            IdentityVerificationResult(
                status=IdentityStatus.VALID,
                pid=persisted.pid,
                expected_create_time=persisted.process_create_time,
                in_expected_job=True,
            )
        ]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)

    divergent_kwargs = {**persisted.__dict__, field_name: divergent_value}
    divergent_identity = CanonicalExecutionIdentity(**divergent_kwargs)

    outcome = engine.terminate_execution(
        TerminationContext(
            identity=divergent_identity,
            job_handle=9001,
            process_handle=9002,
            event_id=f"evt-div-{field_name}",
        )
    )

    _assert_precheck_blocked(
        outcome,
        win32,
        tmp_path / "watchdog.db",
        persisted.execution_id,
        expected_classifications=(OrphanClassification.IDENTITY_MISMATCH,),
    )
    assert validator.calls == []
    store.close()


def test_missing_attempt_record_blocks_termination(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    # Remove the AttemptRecord directly in SQLite to simulate missing AttemptRecord
    with sqlite3.connect(tmp_path / "watchdog.db") as conn:
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("DELETE FROM upload_attempt WHERE attempt_id = ?;", (ident.attempt_id,))
        conn.commit()

    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-missing-attempt")
    )

    _assert_precheck_blocked(
        outcome,
        win32,
        tmp_path / "watchdog.db",
        ident.execution_id,
        expected_classifications=(OrphanClassification.INCONCLUSIVE,),
    )
    assert outcome.error_code == "ATTEMPT_NOT_FOUND"
    assert validator.calls == []
    store.close()


def test_attempt_record_divergent_fencing_token_blocks_termination(tmp_path: Path) -> None:
    ident = _make_identity(fencing_token=10)
    store, outbox = _seed_store(tmp_path, ident)
    # Advance AttemptRecord fencing_token to 11 without changing ExecutionRecord (still 10)
    store.upsert_attempt(
        attempt_id=ident.attempt_id,
        job_id=ident.job_id,
        fencing_token=11,
        hub_status=AttemptHubStatus.IN_PROGRESS,
        last_heartbeat_at_utc="2026-11-19T12:00:00+00:00",
    )

    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    outcome = engine.terminate_execution(
        TerminationContext(
            identity=ident,
            job_handle=9001,
            expected_fencing_token=10,
            event_id="evt-attempt-token-div",
        )
    )

    _assert_precheck_blocked(
        outcome,
        win32,
        tmp_path / "watchdog.db",
        ident.execution_id,
        expected_classifications=(OrphanClassification.IDENTITY_MISMATCH,),
    )
    assert outcome.error_code == "FENCING_TOKEN_MISMATCH"
    assert validator.calls == []
    # Verify fencing tokens were NOT modified
    assert store.get_attempt(ident.attempt_id).fencing_token == 11  # type: ignore[union-attr]
    assert store.get_execution(ident.execution_id).fencing_token == 10  # type: ignore[union-attr]
    store.close()


@pytest.mark.parametrize(
    "blocked_status,expected_error_code",
    [
        (ExecutionStatus.CLOSED, "EXECUTION_STATUS_CLOSED"),
        (ExecutionStatus.INCONCLUSIVE, "EXECUTION_STATUS_INCONCLUSIVE"),
    ],
)
def test_non_terminable_execution_status_blocks_termination(
    tmp_path: Path,
    blocked_status: ExecutionStatus,
    expected_error_code: str,
) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident, status=blocked_status)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id=f"evt-status-{blocked_status.value}")
    )

    _assert_precheck_blocked(
        outcome,
        win32,
        tmp_path / "watchdog.db",
        ident.execution_id,
        expected_classifications=(OrphanClassification.INCONCLUSIVE,),
    )
    assert outcome.error_code == expected_error_code
    assert validator.calls == []
    store.close()


def test_missing_process_identity_record_blocks_termination(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    with sqlite3.connect(tmp_path / "watchdog.db") as conn:
        conn.execute("DELETE FROM process_identity WHERE execution_id = ?;", (ident.execution_id,))
        conn.commit()

    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-missing-proc-id")
    )

    _assert_precheck_blocked(
        outcome,
        win32,
        tmp_path / "watchdog.db",
        ident.execution_id,
        expected_classifications=(OrphanClassification.INCONCLUSIVE,),
    )
    assert outcome.error_code == "PROCESS_IDENTITY_NOT_FOUND"
    assert validator.calls == []
    store.close()


def test_corrupted_process_identity_checksum_blocks_termination(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    with sqlite3.connect(tmp_path / "watchdog.db") as conn:
        conn.execute(
            "UPDATE process_identity SET checksum_sha256 = 'deadbeef' WHERE execution_id = ?;",
            (ident.execution_id,),
        )
        conn.commit()

    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-corrupt-proc-id")
    )

    _assert_precheck_blocked(
        outcome,
        win32,
        tmp_path / "watchdog.db",
        ident.execution_id,
        expected_classifications=(OrphanClassification.INCONCLUSIVE,),
    )
    assert outcome.error_code == "PROCESS_IDENTITY_CORRUPTED"
    assert validator.calls == []
    store.close()


def test_exact_persisted_identity_match_allows_termination(tmp_path: Path) -> None:
    """Positive test: exact match across ExecutionRecord, AttemptRecord, ProcessIdentityRecord and context proceeds."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident, status=ExecutionStatus.TERMINATING)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [
            IdentityVerificationResult(
                status=IdentityStatus.VALID,
                pid=ident.pid,
                expected_create_time=ident.process_create_time,
                actual_create_time=ident.process_create_time,
                in_expected_job=True,
                reason="Exact persisted identity verified",
            )
        ]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)
    outcome = engine.terminate_execution(
        TerminationContext(
            identity=ident,
            job_handle=9001,
            process_handle=9002,
            expected_fencing_token=ident.fencing_token,
            event_id="evt-exact-match",
        )
    )

    assert outcome.verification_result == TerminationVerificationResult.TREE_TERMINATED
    assert outcome.fully_completed is True
    assert len(win32.terminate_calls) == 1
    assert len(validator.calls) == 2
    assert validator.calls[0][0] == ident
    assert validator.calls[1][0] == ident
    store.close()


# ---------------------------------------------------------------------------
# Stable event_id Idempotency Tests (Corrective Round - Section 6)
# ---------------------------------------------------------------------------


def test_stable_event_id_without_caller_key_prevents_duplication_and_overwrites(tmp_path: Path) -> None:
    from desktop_worker.watchdog.termination_evidence import (
        TerminationEvidencePayload,
        TerminationEvidenceRecorder,
    )

    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    recorder = TerminationEvidenceRecorder(store, outbox_store=outbox)

    payload = TerminationEvidencePayload(
        execution_id=ident.execution_id,
        attempt_id=ident.attempt_id,
        job_id=ident.job_id,
        worker_id=ident.worker_id,
        fencing_token=ident.fencing_token,
        pid=ident.pid,
        process_create_time=ident.process_create_time,
        job_object_name=ident.job_object_name,
        identity_validation_status="VALID",
        identity_validation_reason="verified",
        termination_request_ok=True,
        wait_result=WAIT_OBJECT_0,
        active_processes_before=1,
        active_processes_after=0,
        pids_before=[ident.pid],
        pids_after=[],
        pids_truncated=False,
        termination_requested_at_utc="2026-11-19T12:00:00+00:00",
        verified_at_utc="2026-11-19T12:00:01+00:00",
        exit_code=1,
        termination_reason="ORPHAN_EXECUTION",
        watchdog_version="2.7.0",
        verification_result=TerminationVerificationResult.TREE_TERMINATED,
    )

    # 1. Calling build_evidence_record twice without event_id yields the exact same stable event_id
    rec1 = recorder.build_evidence_record(payload)
    rec2 = recorder.build_evidence_record(payload)
    assert rec1.event_id == rec2.event_id
    assert rec1.event_id.startswith("term-")

    # 2. Repeating persist does not duplicate evidence or outbox event
    res1 = recorder.persist(rec1)
    res2 = recorder.persist(rec2)
    assert res1.persisted is True and res1.idempotent_duplicate is False
    assert res2.persisted is True and res2.idempotent_duplicate is True
    with sqlite3.connect(tmp_path / "watchdog.db") as conn:
        ev_count = conn.execute(
            "SELECT COUNT(*) FROM termination_evidence WHERE execution_id = ?;",
            (ident.execution_id,),
        ).fetchone()[0]
    assert ev_count == 1
    assert len(outbox.get_pending_events()) == 1

    # 3. Same event_id with divergent payload raises conflict and never overwrites previous event
    divergent_payload = TerminationEvidencePayload(
        **{**payload.__dict__, "termination_reason": "CONFLICTING_REASON"}
    )
    rec_conflict = recorder.build_evidence_record(divergent_payload, event_id=rec1.event_id)
    res_conflict = recorder.persist(rec_conflict)
    assert res_conflict.persisted is False
    assert "conflicting content" in (res_conflict.error or "").lower()

    # Original evidence and outbox event remain intact
    persisted_ev = store.get_evidence(rec1.event_id)
    assert persisted_ev is not None
    assert persisted_ev.termination_reason == "ORPHAN_EXECUTION"
    persisted_outbox = outbox.get_event(rec1.event_id)
    assert persisted_outbox is not None
    assert json.loads(persisted_outbox.payload_json)["termination_reason"] == "ORPHAN_EXECUTION"
    store.close()


def test_real_termination_flow_twice_is_idempotent_and_preserves_original_timestamps(tmp_path: Path) -> None:
    """Two real terminate_execution() calls without caller event_id recover original evidence & timestamps."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    win32.accounting_sequence = [
        BasicAccountingInfo(active_processes=1, total_processes=1, total_terminated_processes=0),
        BasicAccountingInfo(active_processes=0, total_processes=1, total_terminated_processes=1),
        BasicAccountingInfo(active_processes=0, total_processes=1, total_terminated_processes=1),
    ]
    win32.pid_list_sequence = [
        (True, [ident.pid], 1, 1, None),
        (True, [], 0, 0, None),
        (True, [], 0, 0, None),
    ]
    validator = FakeIdentityValidator(
        [
            IdentityVerificationResult(
                status=IdentityStatus.VALID,
                pid=ident.pid,
                expected_create_time=ident.process_create_time,
                actual_create_time=ident.process_create_time,
                in_expected_job=True,
                reason="verified",
            ),
            IdentityVerificationResult(
                status=IdentityStatus.VALID,
                pid=ident.pid,
                expected_create_time=ident.process_create_time,
                actual_create_time=ident.process_create_time,
                in_expected_job=True,
                reason="verified",
            ),
            IdentityVerificationResult(
                status=IdentityStatus.PROCESS_NOT_FOUND,
                pid=ident.pid,
                expected_create_time=ident.process_create_time,
                reason="already exited",
            ),
        ]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)

    first = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, process_handle=9002)
    )
    assert first.verification_result == TerminationVerificationResult.TREE_TERMINATED
    assert first.evidence_persisted is True
    assert first.outbox_enqueued is True
    assert first.evidence_record is not None

    time.sleep(0.02)

    second = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, process_handle=9002)
    )
    assert second.verification_result == TerminationVerificationResult.TREE_TERMINATED
    assert second.evidence_persisted is True
    assert second.outbox_enqueued is True
    assert second.evidence_record is not None
    assert second.evidence_record.event_id == first.evidence_record.event_id
    # Original timestamps must be preserved
    assert (
        second.evidence_record.termination_requested_at_utc
        == first.evidence_record.termination_requested_at_utc
    )
    assert second.evidence_record.verified_at_utc == first.evidence_record.verified_at_utc
    assert second.evidence_record.checksum_sha256 == first.evidence_record.checksum_sha256

    with sqlite3.connect(tmp_path / "watchdog.db") as conn:
        ev_count = conn.execute(
            "SELECT COUNT(*) FROM termination_evidence WHERE execution_id = ?;",
            (ident.execution_id,),
        ).fetchone()[0]
    assert ev_count == 1
    assert len(outbox.get_pending_events()) == 1
    store.close()


def test_real_termination_flow_idempotent_after_process_restart(tmp_path: Path) -> None:
    """Reopening StateStore/OutboxStore (simulated restart) and repeating termination is idempotent."""
    db_path = tmp_path / "watchdog.db"
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32_1 = FakeWin32Api()
    validator_1 = FakeIdentityValidator(
        [
            IdentityVerificationResult(
                status=IdentityStatus.VALID,
                pid=ident.pid,
                expected_create_time=ident.process_create_time,
                actual_create_time=ident.process_create_time,
                in_expected_job=True,
                reason="verified",
            )
        ]
    )
    engine_1 = SafeTerminationEngine(store, outbox_store=outbox, win32=win32_1, identity_validator=validator_1)
    engine_1.register_job_handle(ident.execution_id, 9001)
    first = engine_1.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, process_handle=9002)
    )
    assert first.fully_completed is True
    assert first.evidence_record is not None
    orig_event_id = first.evidence_record.event_id
    orig_req_ts = first.evidence_record.termination_requested_at_utc
    orig_ver_ts = first.evidence_record.verified_at_utc
    store.close()

    # Simulate process restart
    time.sleep(0.02)
    store2 = StateStore(db_path)
    outbox2 = OutboxStore(store2)
    win32_2 = FakeWin32Api()
    win32_2.accounting_sequence = [
        BasicAccountingInfo(active_processes=0, total_processes=1, total_terminated_processes=1)
    ]
    win32_2.pid_list_sequence = [(True, [], 0, 0, None)]
    validator_2 = FakeIdentityValidator(
        [
            IdentityVerificationResult(
                status=IdentityStatus.PROCESS_NOT_FOUND,
                pid=ident.pid,
                expected_create_time=ident.process_create_time,
                reason="process exited before restart",
            )
        ]
    )
    engine_2 = SafeTerminationEngine(store2, outbox_store=outbox2, win32=win32_2, identity_validator=validator_2)
    engine_2.register_job_handle(ident.execution_id, 9001)
    second = engine_2.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001)
    )
    assert second.fully_completed is True
    assert second.evidence_record is not None
    assert second.evidence_record.event_id == orig_event_id
    assert second.evidence_record.termination_requested_at_utc == orig_req_ts
    assert second.evidence_record.verified_at_utc == orig_ver_ts
    assert win32_2.terminate_calls == []
    assert len(outbox2.get_pending_events()) == 1
    store2.close()


# ---------------------------------------------------------------------------
# Job Object Handle Binding & Nested Job Guard Tests
# ---------------------------------------------------------------------------


def test_registered_job_handle_mismatch_blocks_termination_even_if_validator_valid(tmp_path: Path) -> None:
    """A handle belonging to another/outer Job Object must never authorize TerminateJobObject."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [
            IdentityVerificationResult(
                status=IdentityStatus.VALID,
                pid=ident.pid,
                expected_create_time=ident.process_create_time,
                actual_create_time=ident.process_create_time,
                in_expected_job=True,
                reason="Nested job would report IsProcessInJob=True",
            )
        ]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)

    # Pass a different valid handle (e.g. outer/parent Job Object handle 8888)
    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=8888, process_handle=9002)
    )

    assert win32.terminate_calls == []
    assert validator.calls == []
    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.classification == OrphanClassification.IDENTITY_MISMATCH
    assert outcome.error_code == "JOB_HANDLE_MISMATCH"
    assert outcome.fully_completed is False
    store.close()


def test_expected_job_handle_mismatch_blocks_termination(tmp_path: Path) -> None:
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 7777)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=8888, expected_job_handle=9001)
    )

    assert win32.terminate_calls == []
    assert outcome.error_code == "JOB_HANDLE_MISMATCH"
    assert outcome.classification == OrphanClassification.IDENTITY_MISMATCH
    assert outcome.fully_completed is False
    store.close()


def test_outer_job_handle_containing_watchdog_pid_blocks_termination(tmp_path: Path) -> None:
    import os

    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = FakeWin32Api()
    win32.pid_list_sequence = [(True, [ident.pid, os.getpid()], 2, 2, None)]
    validator = FakeIdentityValidator(
        [IdentityVerificationResult(status=IdentityStatus.VALID, pid=ident.pid, expected_create_time=ident.process_create_time, in_expected_job=True)]
    )
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=validator)
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, process_handle=9002)
    )

    assert win32.terminate_calls == []
    assert outcome.error_code == "JOB_HANDLE_CONTAINS_WATCHDOG"
    assert outcome.classification == OrphanClassification.IDENTITY_MISMATCH
    assert outcome.fully_completed is False
    store.close()


# ---------------------------------------------------------------------------
# Mandatory Job Object Handle Authorization Tests (Sprint 2.7 - Task 6 Corrective)
# ---------------------------------------------------------------------------


class HandleAwareFakeWin32Api(FakeWin32Api):
    """FakeWin32Api supporting handle validity, close, and CompareObjectHandles."""

    def __init__(self) -> None:
        super().__init__()
        self.valid_handles: set[int] = set()
        self.closed_handles: list[int] = []
        # Map handle -> logical kernel object identity (e.g. "job-A", "job-B")
        self.handle_objects: dict[int, str] = {}

    def add_handle(self, handle: int, obj_id: str) -> None:
        self.valid_handles.add(handle)
        self.handle_objects[handle] = obj_id

    def close_handle(self, handle: int) -> bool:
        if handle in self.valid_handles:
            self.valid_handles.remove(handle)
            self.closed_handles.append(handle)
            return True
        return False

    def is_handle_valid(self, handle: int) -> bool:
        return handle in self.valid_handles

    def compare_object_handles(self, handle_a: int, handle_b: int) -> bool:
        if handle_a not in self.valid_handles or handle_b not in self.valid_handles:
            return False
        return self.handle_objects.get(handle_a) == self.handle_objects.get(handle_b)


def _valid_validator(ident: CanonicalExecutionIdentity) -> FakeIdentityValidator:
    return FakeIdentityValidator(
        [
            IdentityVerificationResult(
                status=IdentityStatus.VALID,
                pid=ident.pid,
                expected_create_time=ident.process_create_time,
                actual_create_time=ident.process_create_time,
                in_expected_job=True,
                reason="Process identity verified",
            )
        ]
    )


def _assert_no_confirmed_evidence(store: StateStore, outbox: OutboxStore) -> None:
    """Assert that NO confirmed termination evidence or TERMINATION_CONFIRMED outbox event exists."""
    events = outbox.get_pending_events()
    confirmed_outbox = [
        e for e in events if e.event_type == TerminationOutboxEventType.TERMINATION_CONFIRMED.value
    ]
    assert confirmed_outbox == [], f"Unexpected TERMINATION_CONFIRMED outbox event: {confirmed_outbox}"
    conn = store._conn  # noqa: SLF001
    rows = conn.execute(
        "SELECT event_id, verification_result FROM termination_evidence "
        "WHERE verification_result = 'TREE_TERMINATED';"
    ).fetchall()
    assert rows == [], f"Unexpected TREE_TERMINATED evidence persisted: {rows}"


def test_auth_1_execution_without_registered_handle_blocks_even_with_expected_handle(tmp_path: Path) -> None:
    """Negative 1: Execution without registered handle MUST block fail-closed.
    Neither expected_job_handle nor job_object_name can substitute."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = HandleAwareFakeWin32Api()
    win32.add_handle(9001, "job-1")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_valid_validator(ident))

    outcome = engine.terminate_execution(
        TerminationContext(
            identity=ident,
            job_handle=9001,
            expected_job_handle=9001,  # Must NOT substitute for explicit registration
            process_handle=9002,
            event_id="evt-auth-1",
        )
    )

    assert win32.terminate_calls == []
    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
    assert outcome.fully_completed is False
    assert outcome.evidence_persisted is False
    _assert_no_confirmed_evidence(store, outbox)


def test_auth_2_valid_handle_of_other_job_object_blocks(tmp_path: Path) -> None:
    """Negative 2: Valid handle belonging to a different Job Object MUST block."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = HandleAwareFakeWin32Api()
    win32.add_handle(9001, "job-A")
    win32.add_handle(9002, "job-B")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_valid_validator(ident))
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9002, event_id="evt-auth-2")
    )

    assert win32.terminate_calls == []
    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.classification == OrphanClassification.IDENTITY_MISMATCH
    assert outcome.error_code == "JOB_HANDLE_MISMATCH"
    assert outcome.fully_completed is False
    _assert_no_confirmed_evidence(store, outbox)


def test_auth_3_external_nested_job_object_handle_blocks(tmp_path: Path) -> None:
    """Negative 3: Handle of an outer/nested Job Object MUST block."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = HandleAwareFakeWin32Api()
    win32.add_handle(9001, "job-inner-isolated")
    win32.add_handle(9999, "job-outer-nested")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_valid_validator(ident))
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9999, event_id="evt-auth-3")
    )

    assert win32.terminate_calls == []
    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.classification == OrphanClassification.IDENTITY_MISMATCH
    assert outcome.error_code == "JOB_HANDLE_MISMATCH"
    assert outcome.fully_completed is False
    _assert_no_confirmed_evidence(store, outbox)


def test_auth_4_closed_handle_blocks(tmp_path: Path) -> None:
    """Negative 4: Closed Job Object handle MUST block."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = HandleAwareFakeWin32Api()
    win32.add_handle(9001, "job-A")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_valid_validator(ident))
    engine.register_job_handle(ident.execution_id, 9001)

    # Close handle before termination
    win32.close_handle(9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-auth-4")
    )

    assert win32.terminate_calls == []
    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.error_code == "INVALID_OR_CLOSED_JOB_HANDLE"
    assert outcome.fully_completed is False
    _assert_no_confirmed_evidence(store, outbox)


def test_auth_5_invalid_handle_blocks(tmp_path: Path) -> None:
    """Negative 5: Invalid handle (<= 0 or never opened) MUST block."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = HandleAwareFakeWin32Api()
    win32.add_handle(9001, "job-A")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_valid_validator(ident))
    engine.register_job_handle(ident.execution_id, 9001)

    # Case A: non-positive handle
    out_zero = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=0, event_id="evt-auth-5a")
    )
    assert win32.terminate_calls == []
    assert out_zero.error_code == "MISSING_JOB_HANDLE"
    assert out_zero.fully_completed is False

    # Case B: positive integer that is not a live handle
    out_invalid = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=123456, event_id="evt-auth-5b")
    )
    assert win32.terminate_calls == []
    assert out_invalid.error_code == "INVALID_OR_CLOSED_JOB_HANDLE"
    assert out_invalid.fully_completed is False
    _assert_no_confirmed_evidence(store, outbox)


def test_auth_6_missing_authorization_after_restart_blocks(tmp_path: Path) -> None:
    """Negative 6: After Watchdog restart, authorization registry is empty -> MUST block."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = HandleAwareFakeWin32Api()
    win32.add_handle(9001, "job-A")

    # Engine 1 had authorization
    engine_1 = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_valid_validator(ident))
    engine_1.register_job_handle(ident.execution_id, 9001)
    assert engine_1.has_authorized_job_handle(ident.execution_id) is True

    # Simulate Watchdog restart: new SafeTerminationEngine on the same StateStore
    engine_restarted = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_valid_validator(ident))
    assert engine_restarted.has_authorized_job_handle(ident.execution_id) is False

    outcome = engine_restarted.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-auth-6")
    )

    assert win32.terminate_calls == []
    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
    assert outcome.fully_completed is False
    _assert_no_confirmed_evidence(store, outbox)


def test_auth_7_correctly_duplicated_handle_across_processes_allows_termination(tmp_path: Path) -> None:
    """Positive 7: Distinct numeric handle duplicated from the SAME kernel Job Object allows termination."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = HandleAwareFakeWin32Api()
    # Two different handle numbers in Watchdog's table referring to the same kernel Job Object
    win32.add_handle(5001, "kernel-job-obj-1")
    win32.add_handle(7002, "kernel-job-obj-1")

    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_valid_validator(ident))
    ok, _msg = engine.accept_transferred_job_handle(
        ident.execution_id,
        5001,
        attempt_id=ident.attempt_id,
        fencing_token=ident.fencing_token,
        source_pid=12345,
    )
    assert ok is True

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=7002, process_handle=9002, event_id="evt-auth-7")
    )

    assert outcome.verification_result == TerminationVerificationResult.TREE_TERMINATED
    assert outcome.fully_completed is True
    assert len(win32.terminate_calls) == 1
    assert win32.terminate_calls[0][0] == 7002

    # Unregister closes the owned duplicated handle (5001)
    engine.unregister_job_handle(ident.execution_id, close_if_owned=True)
    assert 5001 in win32.closed_handles


def test_auth_8_duplicated_handle_for_different_object_blocks(tmp_path: Path) -> None:
    """Negative 8: Duplicated handle referring to a DIFFERENT kernel object MUST block."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = HandleAwareFakeWin32Api()
    win32.add_handle(5001, "kernel-job-obj-1")
    win32.add_handle(7002, "kernel-job-obj-OTHER")

    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_valid_validator(ident))
    engine.register_job_handle(ident.execution_id, 5001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=7002, event_id="evt-auth-8")
    )

    assert win32.terminate_calls == []
    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.classification == OrphanClassification.IDENTITY_MISMATCH
    assert outcome.error_code == "JOB_HANDLE_MISMATCH"
    assert outcome.fully_completed is False
    _assert_no_confirmed_evidence(store, outbox)


def test_auth_9_watchdog_daemon_without_authorization_never_calls_terminate(tmp_path: Path) -> None:
    """Negative 9: WatchdogDaemon requesting termination without handle authorization NEVER calls TerminateJobObject."""
    import uuid
    from desktop_worker.watchdog.watchdog_daemon import WatchdogConfig, WatchdogDaemon

    db_path = tmp_path / "wd_no_auth.db"
    worker_id = f"worker-noauth-{uuid.uuid4().hex[:8]}"
    ident = _make_identity(worker_id=worker_id)
    store = StateStore(db_path)
    outbox = OutboxStore(store)
    store.upsert_attempt(
        attempt_id=ident.attempt_id,
        job_id=ident.job_id,
        fencing_token=ident.fencing_token,
        hub_status=AttemptHubStatus.IN_PROGRESS,
        last_heartbeat_at_utc="2026-11-19T12:00:00+00:00",
    )
    store.record_execution(ident, boot_time_utc="2026-11-19T12:00:00+00:00", status=ExecutionStatus.RUNNING)

    win32 = HandleAwareFakeWin32Api()
    win32.add_handle(9001, "job-A")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_valid_validator(ident))
    # Intentionally DO NOT register handle on engine

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
    daemon.start()
    try:
        outcome = daemon.request_safe_termination(
            TerminationContext(identity=ident, job_handle=9001, event_id="evt-auth-9")
        )
        assert win32.terminate_calls == []
        assert outcome.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
        assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
        assert outcome.fully_completed is False
        _assert_no_confirmed_evidence(store, outbox)
    finally:
        daemon.stop()
        store.close()


def test_auth_10_revalidation_immediately_before_terminate_job_object_blocks_toctou(tmp_path: Path) -> None:
    """Negative 10 (TOCTOU): If handle authorization is revoked/closed right before TerminateJobObject,
    the immediate pre-termination re-check MUST block and never call TerminateJobObject."""
    ident = _make_identity()
    store, outbox = _seed_store(tmp_path, ident)
    win32 = HandleAwareFakeWin32Api()
    win32.add_handle(9001, "job-A")

    engine_holder: dict[str, SafeTerminationEngine] = {}

    class _RevokingValidator:
        def __init__(self) -> None:
            self.calls = 0

        def verify(self, expected, *, job_handle=None, tolerance_ticks=0):
            self.calls += 1
            if self.calls == 2:
                # Simulate handle being revoked/closed right before TerminateJobObject
                engine_holder["engine"].unregister_job_handle(ident.execution_id)
            return IdentityVerificationResult(
                status=IdentityStatus.VALID,
                pid=expected.pid,
                expected_create_time=expected.process_create_time,
                actual_create_time=expected.process_create_time,
                in_expected_job=True,
                reason="verified",
            )

    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_RevokingValidator())
    engine_holder["engine"] = engine
    engine.register_job_handle(ident.execution_id, 9001)

    outcome = engine.terminate_execution(
        TerminationContext(identity=ident, job_handle=9001, event_id="evt-auth-10-toctou")
    )

    assert win32.terminate_calls == []
    assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert outcome.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
    assert outcome.fully_completed is False
    _assert_no_confirmed_evidence(store, outbox)
