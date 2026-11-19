"""Unit tests for Watchdog State Store (Sprint 2.7 - Task 3)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from desktop_worker.watchdog.identity import CanonicalExecutionIdentity
from desktop_worker.watchdog.state_store import (
    SCHEMA_VERSION,
    AttemptHubStatus,
    CheckpointRecord,
    ExecutionStatus,
    IntegrityStatus,
    StateStore,
    StateStoreConflictError,
    StateStoreCorruptionError,
    StateStorePersistenceError,
    StateStoreSchemaError,
    TerminationEvidenceRecord,
)


def _sample_identity(
    execution_id: str = "exec-1",
    attempt_id: str = "att-1",
    fencing_token: int = 5,
    pid: int = 4321,
    process_create_time: int = 133750000001234567,
) -> CanonicalExecutionIdentity:
    return CanonicalExecutionIdentity(
        execution_id=execution_id,
        attempt_id=attempt_id,
        job_id="job-1",
        tenant_id="tenant-1",
        channel_id="channel-1",
        worker_id="worker-1",
        fencing_token=fencing_token,
        pid=pid,
        process_create_time=process_create_time,
        job_object_name=f"Local\\DCF_UPLOAD_{attempt_id}_{execution_id}",
        browser_user_data_dir="C:\\profiles\\channel-1",
    )


def test_sqlite_initialization_and_pragmas_wal_synchronous_full(tmp_path: Path) -> None:
    db_path = tmp_path / "watchdog_state.db"
    with StateStore(db_path) as store:
        pragmas = store.get_pragmas()
        assert pragmas["journal_mode"] == "wal"
        assert pragmas["synchronous"] == 2  # FULL == 2 in SQLite
        assert pragmas["foreign_keys"] == 1
        assert pragmas["busy_timeout"] == 5000


def test_create_and_recover_execution_with_filetime_and_fencing(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    ident = _sample_identity(process_create_time=133750000009876543, fencing_token=42)

    with StateStore(db_path) as store:
        created = store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")
        assert created.execution_id == "exec-1"
        assert created.fencing_token == 42
        assert len(created.checksum_sha256) == 64

        recovered = store.get_execution("exec-1")
        assert recovered is not None
        assert recovered.fencing_token == 42
        assert recovered.status == ExecutionStatus.RUNNING

        identities = store.get_process_identities("exec-1")
        assert len(identities) == 1
        assert isinstance(identities[0].process_create_time, int)
        assert identities[0].process_create_time == 133750000009876543


def test_create_and_recover_attempt_and_heartbeat(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    with StateStore(db_path) as store:
        att = store.upsert_attempt("att-1", "job-1", fencing_token=3, hub_status=AttemptHubStatus.INTENT)
        assert att.fencing_token == 3
        assert att.hub_status == AttemptHubStatus.INTENT

        updated = store.update_heartbeat(
            "att-1", expected_fencing_token=3, heartbeat_at_utc="2026-11-19T01:00:00Z"
        )
        assert updated.last_heartbeat_at_utc == "2026-11-19T01:00:00Z"

        fetched = store.get_attempt("att-1")
        assert fetched is not None
        assert fetched.last_heartbeat_at_utc == "2026-11-19T01:00:00Z"


def test_transactional_update_and_reopen_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    ident = _sample_identity(fencing_token=7)

    with StateStore(db_path) as store:
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")
        store.update_execution_status("exec-1", ExecutionStatus.TERMINATING, expected_fencing_token=7)

    with StateStore(db_path) as reopened:
        rec = reopened.get_execution("exec-1")
        assert rec is not None
        assert rec.status == ExecutionStatus.TERMINATING


def test_rollback_on_write_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    ident = _sample_identity(fencing_token=1)

    with StateStore(db_path) as store:
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")
        with pytest.raises(StateStorePersistenceError):
            with store._transaction() as conn:
                conn.execute(
                    "UPDATE upload_execution SET status = 'CLOSED' WHERE execution_id = 'exec-1';"
                )
                raise RuntimeError("Simulated disk/write failure mid-transaction")

        rec = store.get_execution("exec-1")
        assert rec is not None
        assert rec.status == ExecutionStatus.RUNNING


def test_stale_fencing_token_rejected_on_attempt_and_execution(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    ident = _sample_identity(fencing_token=10)

    with StateStore(db_path) as store:
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")
        store.upsert_attempt("att-1", "job-1", fencing_token=10, hub_status=AttemptHubStatus.IN_PROGRESS)

        with pytest.raises(StateStoreConflictError):
            store.upsert_attempt("att-1", "job-1", fencing_token=9, hub_status=AttemptHubStatus.TERMINATED)

        with pytest.raises(StateStoreConflictError):
            store.update_execution_status("exec-1", ExecutionStatus.CLOSED, expected_fencing_token=9)

        with pytest.raises(StateStoreConflictError):
            store.update_heartbeat("att-1", expected_fencing_token=9)


def test_detect_invalid_checksum_and_corrupted_record_as_inconclusive(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    ident = _sample_identity()

    with StateStore(db_path) as store:
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")

    # Tamper directly with SQLite row to simulate corruption
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.execute("UPDATE upload_execution SET status = 'CLOSED' WHERE execution_id = 'exec-1';")
    raw_conn.commit()
    raw_conn.close()

    with StateStore(db_path) as store:
        with pytest.raises(StateStoreCorruptionError, match="INCONCLUSIVE"):
            store.get_execution("exec-1")

        reports = store.verify_integrity()
        exec_reports = [r for r in reports if r.table == "upload_execution"]
        assert len(exec_reports) == 1
        assert exec_reports[0].status == IntegrityStatus.INCONCLUSIVE
        assert exec_reports[0].raw_diagnostic is not None
        assert exec_reports[0].raw_diagnostic["status"] == "CLOSED"


def test_detect_incompatible_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    with StateStore(db_path):
        pass

    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.execute("UPDATE schema_metadata SET value = '999' WHERE key = 'schema_version';")
    raw_conn.commit()
    raw_conn.close()

    with pytest.raises(StateStoreSchemaError, match="Incompatible schema_version"):
        StateStore(db_path)


def test_save_evidence_and_checkpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    ident = _sample_identity()
    with StateStore(db_path) as store:
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")
        ev = TerminationEvidenceRecord(
            event_id="evt-1",
            attempt_id="att-1",
            execution_id="exec-1",
            job_id="job-1",
            worker_id="worker-1",
            fencing_token=5,
            pid=4321,
            process_create_time=133750000001234567,
            job_object_name=ident.job_object_name,
            verification_result="TREE_TERMINATED",
            termination_reason="TIMEOUT",
            evidence_source="JOB_OBJECT",
            watchdog_version="2.7.0",
            termination_requested_at_utc="2026-11-19T00:10:00Z",
            verified_at_utc="2026-11-19T00:10:02Z",
            payload_json='{"ok":true}',
        )
        saved_ev = store.save_evidence(ev)
        assert len(saved_ev.checksum_sha256) == 64
        fetched_ev = store.get_evidence("evt-1")
        assert fetched_ev is not None
        assert fetched_ev.verification_result == "TREE_TERMINATED"

        cp = store.save_checkpoint(
            CheckpointRecord(
                worker_id="worker-1",
                last_boot_time_utc="2026-11-19T00:00:00Z",
                last_scan_at_utc="2026-11-19T00:10:05Z",
                watchdog_pid=999,
                watchdog_version="2.7.0",
            )
        )
        assert len(cp.checksum_sha256) == 64


def test_concurrent_writes_thread_safety(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    errors: list[Exception] = []

    with StateStore(db_path) as store:
        def worker(idx: int) -> None:
            try:
                ident = _sample_identity(
                    execution_id=f"exec-{idx}",
                    attempt_id=f"att-{idx}",
                    pid=1000 + idx,
                )
                store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        all_execs = store.list_executions_by_status([ExecutionStatus.RUNNING])
        assert len(all_execs) == 10


def test_safe_close_idempotency_and_post_close_error(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    store.close()
    store.close()  # Idempotent

    with pytest.raises(StateStorePersistenceError, match="closed"):
        store.get_execution("exec-1")


def test_disk_or_sqlite_unavailable_raises_persistence_error(tmp_path: Path) -> None:
    db_path = tmp_path / "unavailable.db"
    with patch("desktop_worker.watchdog.state_store.sqlite3.connect", side_effect=sqlite3.OperationalError("disk I/O error")):
        with pytest.raises(StateStorePersistenceError, match="disk I/O error"):
            StateStore(db_path)


def test_corruption_in_all_five_tables_and_global_verify_continues(tmp_path: Path) -> None:
    db_path = tmp_path / "all_tables.db"
    ident = _sample_identity()
    with StateStore(db_path) as store:
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")
        store.upsert_attempt("att-1", "job-1", fencing_token=5, hub_status=AttemptHubStatus.IN_PROGRESS)
        store.save_evidence(
            TerminationEvidenceRecord(
                event_id="evt-1",
                attempt_id="att-1",
                execution_id="exec-1",
                job_id="job-1",
                worker_id="worker-1",
                fencing_token=5,
                pid=4321,
                process_create_time=133750000001234567,
                job_object_name=ident.job_object_name,
                verification_result="TREE_TERMINATED",
                termination_reason="TIMEOUT",
                evidence_source="JOB_OBJECT",
                watchdog_version="2.7.0",
                termination_requested_at_utc="2026-11-19T00:10:00Z",
                verified_at_utc="2026-11-19T00:10:02Z",
                payload_json='{"ok":true}',
            )
        )
        store.save_checkpoint(
            CheckpointRecord(
                worker_id="worker-1",
                last_boot_time_utc="2026-11-19T00:00:00Z",
                last_scan_at_utc="2026-11-19T00:10:05Z",
                watchdog_pid=999,
                watchdog_version="2.7.0",
            )
        )
        # Second valid execution to prove verification continues after corruption
        store.record_execution(_sample_identity(execution_id="exec-2", attempt_id="att-2", pid=4322), boot_time_utc="2026-11-19T00:00:00Z")

    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE upload_execution SET status = 'CLOSED' WHERE execution_id = 'exec-1';")
    raw.execute("UPDATE upload_attempt SET hub_status = 'CONFIRMED' WHERE attempt_id = 'att-1';")
    raw.execute("UPDATE process_identity SET role = 'TAMPERED' WHERE execution_id = 'exec-1';")
    raw.execute("UPDATE termination_evidence SET termination_reason = 'TAMPERED' WHERE event_id = 'evt-1';")
    raw.execute("UPDATE watchdog_checkpoint SET watchdog_pid = 1 WHERE worker_id = 'worker-1';")
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        reports = store.verify_integrity()
        inconclusive_tables = {r.table for r in reports if r.status == IntegrityStatus.INCONCLUSIVE}
        assert inconclusive_tables == {
            "upload_execution",
            "upload_attempt",
            "process_identity",
            "termination_evidence",
            "watchdog_checkpoint",
        }
        valid_execs = [r for r in reports if r.table == "upload_execution" and r.status == IntegrityStatus.VALID]
        assert len(valid_execs) == 1
        assert valid_execs[0].record_key == "exec-2"


def test_upsert_attempt_rejects_corrupted_existing_record(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt_attempt.db"
    with StateStore(db_path) as store:
        store.upsert_attempt("att-1", "job-1", fencing_token=1, hub_status=AttemptHubStatus.INTENT)

    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE upload_attempt SET hub_status = 'CONFIRMED' WHERE attempt_id = 'att-1';")
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        with pytest.raises(StateStoreCorruptionError, match="INCONCLUSIVE"):
            store.upsert_attempt("att-1", "job-1", fencing_token=2, hub_status=AttemptHubStatus.IN_PROGRESS)


def test_save_evidence_consistency_validations(tmp_path: Path) -> None:
    db_path = tmp_path / "evidence_checks.db"
    ident = _sample_identity(fencing_token=5, pid=4321, process_create_time=133750000001234567)

    def _make_ev(**overrides: object) -> TerminationEvidenceRecord:
        base = dict(
            event_id="evt-1",
            attempt_id="att-1",
            execution_id="exec-1",
            job_id="job-1",
            worker_id="worker-1",
            fencing_token=5,
            pid=4321,
            process_create_time=133750000001234567,
            job_object_name=ident.job_object_name,
            verification_result="TREE_TERMINATED",
            termination_reason="TIMEOUT",
            evidence_source="JOB_OBJECT",
            watchdog_version="2.7.0",
            termination_requested_at_utc="2026-11-19T00:10:00Z",
            verified_at_utc="2026-11-19T00:10:02Z",
            payload_json='{"ok":true}',
        )
        base.update(overrides)
        return TerminationEvidenceRecord(**base)  # type: ignore[arg-type]

    with StateStore(db_path) as store:
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")

        with pytest.raises(StateStorePersistenceError, match="does not exist"):
            store.save_evidence(_make_ev(execution_id="missing-exec"))

        with pytest.raises(StateStoreConflictError, match="attempt_id"):
            store.save_evidence(_make_ev(attempt_id="wrong-att"))

        with pytest.raises(StateStoreConflictError, match="fencing_token"):
            store.save_evidence(_make_ev(fencing_token=99))

        with pytest.raises(StateStoreConflictError, match="persisted identity"):
            store.save_evidence(_make_ev(pid=9999))

        with pytest.raises(StateStoreConflictError, match="persisted identity"):
            store.save_evidence(_make_ev(process_create_time=999))


def test_checkpoint_valid_and_corrupted_recovery(tmp_path: Path) -> None:
    db_path = tmp_path / "checkpoint.db"
    with StateStore(db_path) as store:
        store.save_checkpoint(
            CheckpointRecord(
                worker_id="worker-1",
                last_boot_time_utc="2026-11-19T00:00:00Z",
                last_scan_at_utc="2026-11-19T00:10:05Z",
                watchdog_pid=999,
                watchdog_version="2.7.0",
            )
        )
        cp = store.get_checkpoint("worker-1")
        assert cp is not None
        assert cp.watchdog_pid == 999

    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE watchdog_checkpoint SET watchdog_pid = 123 WHERE worker_id = 'worker-1';")
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        with pytest.raises(StateStoreCorruptionError, match="INCONCLUSIVE"):
            store.get_checkpoint("worker-1")


def test_interrupted_schema_initialization_leaves_no_partial_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "partial_init.db"
    with patch(
        "desktop_worker.watchdog.state_store._SCHEMA_STATEMENTS",
        [
            "CREATE TABLE IF NOT EXISTS schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);",
            "INVALID SQL STATEMENT TO SIMULATE CRASH;",
        ],
    ):
        with pytest.raises(StateStorePersistenceError):
            StateStore(db_path)

    # Reopening with real schema must succeed cleanly (no partial tables left behind)
    with StateStore(db_path) as store:
        assert store.list_executions_by_status() == []


def test_commit_failure_and_rollback_failure_blocks_connection_reuse(tmp_path: Path) -> None:
    db_path = tmp_path / "tx_fail.db"
    ident = _sample_identity()

    class _FailingCommitConn:
        def __init__(self, real: sqlite3.Connection) -> None:
            self._real = real

        def execute(self, sql: str, *args: object) -> object:
            if sql.strip().upper() == "COMMIT;":
                raise sqlite3.OperationalError("simulated commit failure")
            return self._real.execute(sql, *args)

        def close(self) -> None:
            self._real.close()

    with StateStore(db_path) as store:
        store._conn = _FailingCommitConn(store._conn)  # type: ignore[assignment]
        with pytest.raises(StateStorePersistenceError, match="simulated commit failure"):
            store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")

    class _FailingRollbackConn:
        def __init__(self, real: sqlite3.Connection) -> None:
            self._real = real

        def execute(self, sql: str, *args: object) -> object:
            if sql.strip().upper() == "ROLLBACK;":
                raise sqlite3.OperationalError("simulated rollback failure")
            return self._real.execute(sql, *args)

        def close(self) -> None:
            self._real.close()

    store2 = StateStore(db_path)
    store2._conn = _FailingRollbackConn(store2._conn)  # type: ignore[assignment]
    with pytest.raises(StateStorePersistenceError, match="rollback failed"):
        with store2._transaction():
            raise RuntimeError("original write error")

    with pytest.raises(StateStorePersistenceError, match="unsafe state"):
        store2.get_execution("exec-1")


def test_save_evidence_rejects_mismatched_job_object_name_and_attempt_id_in_identity(tmp_path: Path) -> None:
    db_path = tmp_path / "ev_full_identity.db"
    ident = _sample_identity(fencing_token=5, pid=4321, process_create_time=133750000001234567)

    def _make_ev(**overrides: object) -> TerminationEvidenceRecord:
        base = dict(
            event_id="evt-full-1",
            attempt_id="att-1",
            execution_id="exec-1",
            job_id="job-1",
            worker_id="worker-1",
            fencing_token=5,
            pid=4321,
            process_create_time=133750000001234567,
            job_object_name=ident.job_object_name,
            verification_result="TREE_TERMINATED",
            termination_reason="TIMEOUT",
            evidence_source="JOB_OBJECT",
            watchdog_version="2.7.0",
            termination_requested_at_utc="2026-11-19T00:10:00Z",
            verified_at_utc="2026-11-19T00:10:02Z",
            payload_json='{"ok":true}',
        )
        base.update(overrides)
        return TerminationEvidenceRecord(**base)  # type: ignore[arg-type]

    with StateStore(db_path) as store:
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")

        # 1. Different job_object_name must be rejected and not persisted
        with pytest.raises(StateStoreConflictError, match="does not match any persisted identity"):
            store.save_evidence(_make_ev(job_object_name="Local\\DCF_UPLOAD_OTHER"))
        assert store.get_evidence("evt-full-1") is None

        # 2. Valid evidence with all matching fields succeeds
        saved = store.save_evidence(_make_ev())
        assert saved.event_id == "evt-full-1"
        assert store.get_evidence("evt-full-1") is not None


def test_save_evidence_rejects_corrupted_execution_or_corrupted_identity(tmp_path: Path) -> None:
    db_path_exec = tmp_path / "ev_corrupt_exec.db"
    db_path_id = tmp_path / "ev_corrupt_id.db"
    ident = _sample_identity(fencing_token=5, pid=4321, process_create_time=133750000001234567)
    ev = TerminationEvidenceRecord(
        event_id="evt-corrupt-1",
        attempt_id="att-1",
        execution_id="exec-1",
        job_id="job-1",
        worker_id="worker-1",
        fencing_token=5,
        pid=4321,
        process_create_time=133750000001234567,
        job_object_name=ident.job_object_name,
        verification_result="TREE_TERMINATED",
        termination_reason="TIMEOUT",
        evidence_source="JOB_OBJECT",
        watchdog_version="2.7.0",
        termination_requested_at_utc="2026-11-19T00:10:00Z",
        verified_at_utc="2026-11-19T00:10:02Z",
        payload_json='{"ok":true}',
    )

    # Corrupted execution checksum
    with StateStore(db_path_exec) as store:
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")
    raw = sqlite3.connect(str(db_path_exec))
    raw.execute("UPDATE upload_execution SET status = 'CLOSED' WHERE execution_id = 'exec-1';")
    raw.commit()
    raw.close()
    with StateStore(db_path_exec) as store:
        with pytest.raises(StateStoreCorruptionError, match="INCONCLUSIVE"):
            store.save_evidence(ev)
        assert store.get_evidence("evt-corrupt-1") is None

    # Corrupted process_identity checksum (or tampered attempt_id)
    with StateStore(db_path_id) as store:
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")
    raw2 = sqlite3.connect(str(db_path_id))
    raw2.execute("UPDATE process_identity SET attempt_id = 'att-other' WHERE execution_id = 'exec-1';")
    raw2.commit()
    raw2.close()
    with StateStore(db_path_id) as store:
        with pytest.raises(StateStoreCorruptionError, match="INCONCLUSIVE"):
            store.save_evidence(ev)
        assert store.get_evidence("evt-corrupt-1") is None


def test_init_failure_on_schema_version_insert_and_rollback_failure_closes_conn(tmp_path: Path) -> None:
    db_path = tmp_path / "init_rb_fail.db"
    real_connect = sqlite3.connect
    closed_flag = {"closed": False}

    class _FailOnMetaAndRollbackConn:
        def __init__(self, real: sqlite3.Connection) -> None:
            self._real = real

        @property
        def row_factory(self) -> object:
            return self._real.row_factory

        @row_factory.setter
        def row_factory(self, val: object) -> None:
            self._real.row_factory = val

        @property
        def in_transaction(self) -> bool:
            return self._real.in_transaction

        def execute(self, sql: str, *args: object) -> object:
            if "INSERT INTO schema_metadata" in sql:
                raise sqlite3.OperationalError("simulated schema_version insert failure")
            if sql.strip().upper() == "ROLLBACK;":
                raise sqlite3.OperationalError("simulated init rollback failure")
            return self._real.execute(sql, *args)

        def close(self) -> None:
            closed_flag["closed"] = True
            self._real.close()

    def _wrapped_connect(*args: object, **kwargs: object) -> _FailOnMetaAndRollbackConn:
        return _FailOnMetaAndRollbackConn(real_connect(*args, **kwargs))

    with patch("desktop_worker.watchdog.state_store.sqlite3.connect", side_effect=_wrapped_connect):
        with pytest.raises(StateStorePersistenceError) as exc_info:
            StateStore(db_path)

    msg = str(exc_info.value)
    assert "simulated schema_version insert failure" in msg
    assert "simulated init rollback failure" in msg
    assert closed_flag["closed"] is True

    # Reopening cleanly must succeed (no partial schema committed)
    with StateStore(db_path) as store:
        assert store.list_executions_by_status() == []

