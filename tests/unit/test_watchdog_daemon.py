"""Unit tests for WatchdogDaemon (Sprint 2.7 - Task 5)."""

from __future__ import annotations

import math
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

from desktop_worker.watchdog.identity import CanonicalExecutionIdentity
from desktop_worker.watchdog.state_store import (
    AttemptHubStatus,
    ExecutionStatus,
    StateStore,
    StateStorePersistenceError,
)
from desktop_worker.watchdog.watchdog_daemon import (
    EXIT_ALREADY_RUNNING,
    EXIT_CLEAN,
    WAIT_ABANDONED,
    WAIT_FAILED,
    WAIT_OBJECT_0,
    WAIT_TIMEOUT,
    MonitoringStatus,
    MutexAcquireStatus,
    WatchdogAlreadyRunningError,
    WatchdogConfig,
    WatchdogConfigError,
    WatchdogDaemon,
    WatchdogMutexError,
    WindowsNamedMutex,
    format_utc_iso,
)


# ---------------------------------------------------------------------------
# Fake Win32 Mutex API for deterministic unit testing
# ---------------------------------------------------------------------------


class FakeMutexApi:
    """In-memory fake implementing MutexApiProtocol."""

    def __init__(
        self,
        *,
        create_handle: int = 1001,
        create_error: int = 0,
        wait_result: int = WAIT_OBJECT_0,
        last_error: int = 0,
        release_ok: bool = True,
        close_ok: bool = True,
    ) -> None:
        self.create_handle = create_handle
        self.create_error = create_error
        self.wait_result = wait_result
        self.last_error = last_error
        self.release_ok = release_ok
        self.close_ok = close_ok

        self.created_names: List[str] = []
        self.waited_handles: List[Tuple[int, int]] = []
        self.released_handles: List[int] = []
        self.closed_handles: List[int] = []

    def create_mutex(self, name: str) -> Tuple[int, int]:
        self.created_names.append(name)
        return self.create_handle, self.create_error

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        self.waited_handles.append((handle, timeout_ms))
        return self.wait_result

    def release_mutex(self, handle: int) -> bool:
        self.released_handles.append(handle)
        return self.release_ok

    def close_handle(self, handle: int) -> bool:
        self.closed_handles.append(handle)
        return self.close_ok

    def get_last_error(self) -> int:
        return self.last_error


def _make_config(
    tmp_path: Path,
    *,
    worker_id: str = "worker-test-01",
    poll_interval_sec: float = 0.05,
    heartbeat_interval_sec: float = 5.0,
    heartbeat_timeout_sec: float = 30.0,
) -> WatchdogConfig:
    return WatchdogConfig(
        worker_id=worker_id,
        db_path=tmp_path / "watchdog_state.db",
        poll_interval_sec=poll_interval_sec,
        heartbeat_interval_sec=heartbeat_interval_sec,
        heartbeat_timeout_sec=heartbeat_timeout_sec,
    )


def _seed_execution(
    db_path: Path,
    *,
    execution_id: str = "exec-1",
    attempt_id: str = "att-1",
    job_id: str = "job-1",
    fencing_token: int = 1,
    status: ExecutionStatus = ExecutionStatus.RUNNING,
    heartbeat_at_utc: Optional[str] = None,
    create_attempt: bool = True,
) -> None:
    now_iso = format_utc_iso(datetime.now(timezone.utc))
    identity = CanonicalExecutionIdentity(
        execution_id=execution_id,
        attempt_id=attempt_id,
        job_id=job_id,
        tenant_id="tenant-1",
        channel_id="channel-1",
        worker_id="worker-test-01",
        fencing_token=fencing_token,
        pid=43210,
        process_create_time=133500000000000000,
        job_object_name="Local\\DCF_UPLOAD_JOB_exec-1",
        browser_user_data_dir="C:/tmp/profile-1",
    )
    with StateStore(db_path) as store:
        store.record_execution(identity, boot_time_utc=now_iso, status=status)
        if create_attempt:
            store.upsert_attempt(
                attempt_id=attempt_id,
                job_id=job_id,
                fencing_token=fencing_token,
                hub_status=AttemptHubStatus.IN_PROGRESS,
                last_heartbeat_at_utc=heartbeat_at_utc,
            )


# ---------------------------------------------------------------------------
# 1 & 2. Initialization & Configuration Validation
# ---------------------------------------------------------------------------


def test_01_valid_initialization(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    api = FakeMutexApi()
    daemon = WatchdogDaemon(cfg, mutex_api=api)

    assert cfg.mutex_name == "Local\\DCF_WATCHDOG_LOCK_worker-test-01"
    assert not daemon.is_running()
    snap = daemon.health_check()
    assert snap.active is False
    assert snap.mutex_acquired is False
    assert snap.state_store_accessible is False
    assert snap.last_cycle_id is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"worker_id": ""},
        {"worker_id": "   "},
        {"worker_id": "bad\\id"},
        {"db_path": ""},
        {"poll_interval_sec": 0},
        {"poll_interval_sec": -1.0},
        {"poll_interval_sec": math.nan},
        {"poll_interval_sec": math.inf},
        {"heartbeat_interval_sec": 0},
        {"heartbeat_timeout_sec": 0},
        {"heartbeat_interval_sec": 10.0, "heartbeat_timeout_sec": 5.0},
        {"stop_timeout_sec": -0.5},
    ],
)
def test_02_invalid_configuration_rejected(tmp_path: Path, kwargs: Dict) -> None:
    base = dict(
        worker_id="worker-01",
        db_path=tmp_path / "state.db",
        poll_interval_sec=1.0,
        heartbeat_interval_sec=5.0,
        heartbeat_timeout_sec=15.0,
        stop_timeout_sec=5.0,
    )
    base.update(kwargs)
    with pytest.raises(WatchdogConfigError):
        WatchdogConfig(**base)  # type: ignore[arg-type]


def test_02b_from_env_missing_or_invalid(tmp_path: Path) -> None:
    with pytest.raises(WatchdogConfigError):
        WatchdogConfig.from_env(worker_id="w1", db_path=tmp_path / "s.db", env={})

    with pytest.raises(WatchdogConfigError):
        WatchdogConfig.from_env(
            worker_id="w1",
            db_path=tmp_path / "s.db",
            env={
                "WATCHDOG_POLL_INTERVAL_SEC": "abc",
                "WATCHDOG_HEARTBEAT_INTERVAL_SEC": "5",
                "WATCHDOG_HEARTBEAT_TIMEOUT_SEC": "15",
            },
        )


# ---------------------------------------------------------------------------
# 3, 4, 5, 6, 7. Mutex Acquisition, Contention, Abandoned, Failed, Idempotent Release
# ---------------------------------------------------------------------------


def test_03_mutex_acquisition_wait_object_0(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    api = FakeMutexApi(wait_result=WAIT_OBJECT_0)
    daemon = WatchdogDaemon(cfg, mutex_api=api)

    res = daemon.start()
    try:
        assert res.status == MutexAcquireStatus.ACQUIRED
        assert res.acquired is True
        assert api.created_names == ["Local\\DCF_WATCHDOG_LOCK_worker-test-01"]
        assert daemon.is_running() is True
        assert daemon.health_check().mutex_acquired is True
    finally:
        daemon.stop()


def test_04_mutex_already_held_blocks_second_instance(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    api = FakeMutexApi(wait_result=WAIT_TIMEOUT)
    daemon = WatchdogDaemon(cfg, mutex_api=api)

    with pytest.raises(WatchdogAlreadyRunningError):
        daemon.start()

    assert daemon.is_running() is False
    assert api.released_handles == []  # Never owned, must NOT call ReleaseMutex
    assert api.closed_handles == [1001]  # Handle must still be closed

    # run() returns EXIT_ALREADY_RUNNING without starting monitoring loop
    daemon2 = WatchdogDaemon(cfg, mutex_api=FakeMutexApi(wait_result=WAIT_TIMEOUT))
    assert daemon2.run(max_cycles=1, install_signals=False) == EXIT_ALREADY_RUNNING


def test_05_mutex_wait_abandoned_triggers_state_store_validation(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    StateStore(cfg.db_path).close()
    api = FakeMutexApi(wait_result=WAIT_ABANDONED)
    daemon = WatchdogDaemon(cfg, mutex_api=api)

    res = daemon.start()
    try:
        assert res.status == MutexAcquireStatus.ABANDONED
        assert res.acquired is True
        cycle = daemon.run_once()
        assert cycle.abandoned_mutex_recovery is True
        assert cycle.overall_status == MonitoringStatus.HEALTHY
        assert cycle.integrity_failures == 0
    finally:
        daemon.stop()


def test_06_mutex_wait_failed_prevents_operation(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    api = FakeMutexApi(wait_result=WAIT_FAILED, last_error=6)
    daemon = WatchdogDaemon(cfg, mutex_api=api)

    with pytest.raises(WatchdogMutexError):
        daemon.start()

    assert daemon.is_running() is False
    assert api.released_handles == []
    assert api.closed_handles == [1001]


def test_07_mutex_release_is_idempotent(tmp_path: Path) -> None:
    api = FakeMutexApi(wait_result=WAIT_OBJECT_0)
    mutex = WindowsNamedMutex("Local\\DCF_WATCHDOG_LOCK_idem", api=api)

    assert mutex.acquire().acquired is True
    assert mutex.is_owned is True

    assert mutex.release() is True
    assert mutex.release() is True  # Duplicate call must be no-op
    assert mutex.is_owned is False
    assert api.released_handles == [1001]
    assert api.closed_handles == [1001]


# ---------------------------------------------------------------------------
# 8 & 9. State Store Open Failure & Corruption
# ---------------------------------------------------------------------------


def test_08_state_store_open_failure_enters_safe_store_unavailable(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    api = FakeMutexApi(wait_result=WAIT_OBJECT_0)

    def failing_factory(_path: object) -> StateStore:
        raise StateStorePersistenceError("disk offline")

    daemon = WatchdogDaemon(cfg, mutex_api=api, store_factory=failing_factory)
    daemon.start()
    try:
        cycle = daemon.run_once()
        assert cycle.overall_status == MonitoringStatus.STORE_UNAVAILABLE
        assert cycle.error_code == "STATE_STORE_UNAVAILABLE"
        snap = daemon.health_check()
        assert snap.state_store_accessible is False
        assert snap.last_cycle_status == MonitoringStatus.STORE_UNAVAILABLE
        assert snap.current_error is not None
    finally:
        daemon.stop()


def test_09_state_store_corrupted_classified_inconclusive(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    now_iso = format_utc_iso(datetime.now(timezone.utc))
    _seed_execution(Path(cfg.db_path), heartbeat_at_utc=now_iso)

    # Corrupt checksum directly in SQLite
    conn = sqlite3.connect(str(cfg.db_path))
    conn.execute("UPDATE upload_execution SET checksum_sha256 = 'corrupted_hex';")
    conn.commit()
    conn.close()

    daemon = WatchdogDaemon(cfg, mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0))
    daemon.start()
    try:
        cycle = daemon.run_once()
        assert cycle.overall_status == MonitoringStatus.INCONCLUSIVE
        assert cycle.integrity_failures >= 1
        assert cycle.error_code == "STATE_STORE_INTEGRITY_FAILURE"
    finally:
        daemon.stop()


# ---------------------------------------------------------------------------
# 10, 11, 12. Healthy, Heartbeat Expired (No Mutation), Inconclusive
# ---------------------------------------------------------------------------


def test_10_healthy_execution_monitoring(tmp_path: Path) -> None:
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
    hb = format_utc_iso(now - timedelta(seconds=5))
    cfg = _make_config(tmp_path, heartbeat_interval_sec=5.0, heartbeat_timeout_sec=30.0)
    _seed_execution(Path(cfg.db_path), heartbeat_at_utc=hb)

    daemon = WatchdogDaemon(
        cfg,
        mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0),
        clock=lambda: now,
    )
    daemon.start()
    try:
        cycle = daemon.run_once()
        assert cycle.overall_status == MonitoringStatus.HEALTHY
        assert cycle.executions_total == 1
        assert len(cycle.observations) == 1
        obs = cycle.observations[0]
        assert obs.monitoring_status == MonitoringStatus.HEALTHY
        assert obs.heartbeat_age_sec == pytest.approx(5.0)
    finally:
        daemon.stop()


def test_11_heartbeat_expired_does_not_mutate_execution_status(tmp_path: Path) -> None:
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
    stale_hb = format_utc_iso(now - timedelta(seconds=120))
    cfg = _make_config(tmp_path, heartbeat_interval_sec=5.0, heartbeat_timeout_sec=30.0)
    _seed_execution(Path(cfg.db_path), status=ExecutionStatus.RUNNING, heartbeat_at_utc=stale_hb)

    daemon = WatchdogDaemon(
        cfg,
        mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0),
        clock=lambda: now,
    )
    daemon.start()
    try:
        cycle = daemon.run_once()
        assert cycle.overall_status == MonitoringStatus.HEARTBEAT_EXPIRED
        assert cycle.observations[0].monitoring_status == MonitoringStatus.HEARTBEAT_EXPIRED
        assert cycle.observations[0].heartbeat_age_sec == pytest.approx(120.0)
    finally:
        daemon.stop()

    # Verify State Store execution status was NOT altered
    with StateStore(cfg.db_path) as store:
        rec = store.get_execution("exec-1")
        assert rec is not None
        assert rec.status == ExecutionStatus.RUNNING


def test_12_inconclusive_execution_state(tmp_path: Path) -> None:
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
    cfg = _make_config(tmp_path)
    _seed_execution(
        Path(cfg.db_path),
        execution_id="exec-inc",
        attempt_id="att-inc",
        status=ExecutionStatus.INCONCLUSIVE,
        create_attempt=False,
    )

    daemon = WatchdogDaemon(
        cfg,
        mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0),
        clock=lambda: now,
    )
    daemon.start()
    try:
        cycle = daemon.run_once()
        assert cycle.overall_status == MonitoringStatus.INCONCLUSIVE
        assert cycle.observations[0].monitoring_status == MonitoringStatus.INCONCLUSIVE
    finally:
        daemon.stop()


# ---------------------------------------------------------------------------
# 13, 14. Periodic Loop & Interruption via stop()
# ---------------------------------------------------------------------------


def test_13_periodic_loop_runs_multiple_cycles(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, poll_interval_sec=0.01)
    daemon = WatchdogDaemon(cfg, mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0))

    cycle_ids: List[str] = []
    orig_run_once = daemon.run_once

    def spy_run_once():
        res = orig_run_once()
        cycle_ids.append(res.cycle_id)
        return res

    daemon.run_once = spy_run_once  # type: ignore[method-assign]
    exit_code = daemon.run(max_cycles=3, install_signals=False)

    assert exit_code == EXIT_CLEAN
    assert len(cycle_ids) == 3
    assert len(set(cycle_ids)) == 3


def test_14_loop_interrupted_promptly_by_stop(tmp_path: Path) -> None:
    # Long poll interval; stop() must wake up wait() immediately
    cfg = _make_config(tmp_path, poll_interval_sec=30.0)
    daemon = WatchdogDaemon(cfg, mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0))

    result_box: List[int] = []
    t = threading.Thread(
        target=lambda: result_box.append(daemon.run(install_signals=False)),
        daemon=True,
    )
    t.start()

    # Wait briefly until daemon is active
    deadline = time.monotonic() + 3.0
    while not daemon.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert daemon.is_running()

    t0 = time.monotonic()
    assert daemon.stop(timeout_sec=3.0) is True
    t.join(timeout=3.0)
    elapsed = time.monotonic() - t0

    assert not t.is_alive()
    assert elapsed < 2.0
    assert result_box == [EXIT_CLEAN]


# ---------------------------------------------------------------------------
# 15 & 16. Exception During Cycle & Recovery After Transient Error
# ---------------------------------------------------------------------------


def test_15_and_16_exception_during_cycle_and_transient_recovery(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, poll_interval_sec=0.01)
    StateStore(cfg.db_path).close()
    attempts = {"count": 0}

    def flaky_store_factory(path: object) -> StateStore:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise StateStorePersistenceError("transient lock failure")
        return StateStore(path)  # type: ignore[arg-type]

    daemon = WatchdogDaemon(
        cfg,
        mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0),
        store_factory=flaky_store_factory,
    )
    daemon.start()
    try:
        # Cycle 1: fails transiently (open_store was called in start() as attempt 1,
        # so let's force an unexpected exception on verify_integrity for cycle 1)
        assert daemon._store is None  # attempt 1 failed during start()

        # Cycle 1 reopens successfully (attempt 2), then simulate transient error
        with patch.object(StateStore, "verify_integrity", side_effect=RuntimeError("boom")):
            c1 = daemon.run_once()
            assert c1.overall_status == MonitoringStatus.INCONCLUSIVE
            assert c1.error_code == "UNEXPECTED_CYCLE_ERROR"

        # Cycle 2 recovers cleanly
        c2 = daemon.run_once()
        assert c2.overall_status == MonitoringStatus.HEALTHY
        assert c2.error_code is None
        assert daemon.health_check().current_error is None
    finally:
        daemon.stop()


# ---------------------------------------------------------------------------
# 17 & 18. Controlled Shutdown & No Process Termination Guarantee
# ---------------------------------------------------------------------------


def test_17_controlled_shutdown_releases_store_and_mutex(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    StateStore(cfg.db_path).close()
    api = FakeMutexApi(wait_result=WAIT_OBJECT_0)
    daemon = WatchdogDaemon(cfg, mutex_api=api)

    daemon.start()
    daemon.run_once()
    assert daemon.health_check().mutex_acquired is True
    assert daemon.health_check().state_store_accessible is True

    assert daemon.stop() is True
    snap = daemon.health_check()
    assert snap.active is False
    assert snap.mutex_acquired is False
    assert snap.state_store_accessible is False
    assert api.released_handles == [1001]
    assert api.closed_handles == [1001]


def test_18_daemon_never_terminates_processes(tmp_path: Path) -> None:
    """Verify WatchdogDaemon does not invoke TerminateJobObject, TerminateProcess, or os.kill."""
    now = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
    stale_hb = format_utc_iso(now - timedelta(seconds=600))
    cfg = _make_config(tmp_path, heartbeat_interval_sec=5.0, heartbeat_timeout_sec=30.0)
    _seed_execution(Path(cfg.db_path), status=ExecutionStatus.RUNNING, heartbeat_at_utc=stale_hb)

    daemon = WatchdogDaemon(
        cfg,
        mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0),
        clock=lambda: now,
    )
    with patch("os.kill", side_effect=AssertionError("os.kill must not be called")):
        daemon.start()
        try:
            cycle = daemon.run_once()
            assert cycle.overall_status == MonitoringStatus.HEARTBEAT_EXPIRED
        finally:
            daemon.stop()


# ---------------------------------------------------------------------------
# 19. Correction 1 — stop() Must NOT Close Store or Release Mutex During Active Cycle
# ---------------------------------------------------------------------------


class SharedFakeMutexApi:
    """Thread-safe in-memory fake mutex shared across multiple daemon instances."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_handle = 2000
        self._held_by_handle: Optional[int] = None
        self.released_handles: List[int] = []
        self.closed_handles: List[int] = []

    def create_mutex(self, name: str) -> Tuple[int, int]:
        with self._lock:
            self._next_handle += 1
            existed = 183 if self._held_by_handle is not None else 0
            return self._next_handle, existed

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        with self._lock:
            if self._held_by_handle is None:
                self._held_by_handle = handle
                return WAIT_OBJECT_0
            return WAIT_TIMEOUT

    def release_mutex(self, handle: int) -> bool:
        with self._lock:
            if self._held_by_handle == handle:
                self._held_by_handle = None
                self.released_handles.append(handle)
                return True
            return False

    def close_handle(self, handle: int) -> bool:
        with self._lock:
            self.closed_handles.append(handle)
            return True

    def get_last_error(self) -> int:
        return 0


def test_19_stop_timeout_keeps_store_open_and_mutex_held_until_cycle_finishes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = _make_config(tmp_path)
    StateStore(cfg.db_path).close()

    shared_api = SharedFakeMutexApi()
    daemon1 = WatchdogDaemon(cfg, mutex_api=shared_api)
    daemon2 = WatchdogDaemon(cfg, mutex_api=shared_api)

    cycle_entered = threading.Event()
    release_cycle = threading.Event()
    cycle_result_box: List[object] = []

    daemon1.start()
    assert daemon1._store is not None
    orig_verify = daemon1._store.verify_integrity

    def blocking_verify():
        cycle_entered.set()
        release_cycle.wait(timeout=5.0)
        return orig_verify()

    daemon1._store.verify_integrity = blocking_verify  # type: ignore[method-assign]

    worker = threading.Thread(
        target=lambda: cycle_result_box.append(daemon1.run_once()),
        daemon=True,
    )
    worker.start()
    assert cycle_entered.wait(timeout=3.0), "Cycle did not start in time"

    # Call stop() with short timeout while cycle is blocked
    with caplog.at_level("ERROR"):
        stopped_cleanly = daemon1.stop(timeout_sec=0.1)

    assert stopped_cleanly is False
    assert any(getattr(r, "error_code", None) == "UNCLEAN_SHUTDOWN" for r in caplog.records)

    # State Store MUST remain open and Mutex MUST remain held while cycle is active
    assert daemon1._store is not None
    assert daemon1._mutex.is_owned is True
    assert shared_api.released_handles == []

    # Second instance MUST NOT be able to acquire the Mutex
    with pytest.raises(WatchdogAlreadyRunningError):
        daemon2.start()

    # Release the blocked cycle and verify subsequent clean shutdown (no deadlock)
    release_cycle.set()
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert len(cycle_result_box) == 1

    assert daemon1.stop(timeout_sec=2.0) is True
    assert daemon1._store is None
    assert daemon1._mutex.is_owned is False
    assert len(shared_api.released_handles) == 1

    # Now second instance CAN acquire the Mutex
    res2 = daemon2.start()
    try:
        assert res2.acquired is True
    finally:
        daemon2.stop()


# ---------------------------------------------------------------------------
# 20. Correction 3 — Strictly Read-Only Outbox Verification
# ---------------------------------------------------------------------------


def _snapshot_db(db_path: Path) -> Dict[str, object]:
    """Capture schema, metadata, and outbox_event rows for before/after comparison."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        master = [
            tuple(r)
            for r in conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name;"
            ).fetchall()
        ]
        meta = [
            tuple(r)
            for r in conn.execute(
                "SELECT key, value FROM schema_metadata ORDER BY key;"
            ).fetchall()
        ]
        outbox_rows = []
        has_outbox = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='outbox_event';"
        ).fetchone()
        if has_outbox:
            outbox_rows = [
                tuple(r)
                for r in conn.execute(
                    "SELECT * FROM outbox_event ORDER BY event_id;"
                ).fetchall()
            ]
        return {"master": master, "meta": meta, "outbox_rows": outbox_rows}
    finally:
        conn.close()


def test_20a_outbox_v2_valid_readonly_no_mutations(tmp_path: Path) -> None:
    from desktop_worker.watchdog.outbox import OutboxStore

    cfg = _make_config(tmp_path)
    with StateStore(cfg.db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-v2-1",
            event_type="EXECUTION_STARTED",
            worker_id=cfg.worker_id,
            payload={"ok": True},
        )

    before = _snapshot_db(Path(cfg.db_path))

    daemon = WatchdogDaemon(cfg, mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0))
    daemon.start()
    try:
        cycle = daemon.run_once()
        assert cycle.overall_status == MonitoringStatus.HEALTHY
        assert cycle.outbox_available is True
        assert cycle.outbox_integrity_failures == 0
    finally:
        daemon.stop()

    after = _snapshot_db(Path(cfg.db_path))
    assert after == before


def test_20b_outbox_v1_classified_inconclusive_without_migration(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    StateStore(cfg.db_path).close()

    # Create a legacy v1 outbox_event table (without claim_count / lease_id)
    conn = sqlite3.connect(str(cfg.db_path))
    conn.execute(
        """
        CREATE TABLE outbox_event (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            execution_id TEXT,
            attempt_id TEXT,
            job_id TEXT,
            worker_id TEXT NOT NULL,
            fencing_token INTEGER,
            payload_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            available_at_utc TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at_utc TEXT,
            delivered_at_utc TEXT,
            last_error TEXT,
            status TEXT NOT NULL,
            lease_owner TEXT,
            lease_expires_at_utc TEXT,
            ack_id TEXT,
            schema_version INTEGER NOT NULL,
            checksum_sha256 TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_metadata (key, value) VALUES ('outbox_schema_version', '1');"
    )
    conn.commit()
    conn.close()

    before = _snapshot_db(Path(cfg.db_path))

    daemon = WatchdogDaemon(cfg, mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0))
    daemon.start()
    try:
        cycle = daemon.run_once()
        assert cycle.overall_status == MonitoringStatus.INCONCLUSIVE
        assert cycle.outbox_available is True
        assert cycle.outbox_integrity_failures >= 1
    finally:
        daemon.stop()

    after = _snapshot_db(Path(cfg.db_path))
    assert after == before


def test_20c_outbox_invalid_checksum_classified_inconclusive_without_mutation(
    tmp_path: Path,
) -> None:
    from desktop_worker.watchdog.outbox import OutboxStore

    cfg = _make_config(tmp_path)
    with StateStore(cfg.db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-corrupt-1",
            event_type="EXECUTION_STARTED",
            worker_id=cfg.worker_id,
            payload={"k": "v"},
        )

    conn = sqlite3.connect(str(cfg.db_path))
    conn.execute(
        "UPDATE outbox_event SET checksum_sha256 = 'deadbeef' WHERE event_id = 'evt-corrupt-1';"
    )
    conn.commit()
    conn.close()

    before = _snapshot_db(Path(cfg.db_path))

    daemon = WatchdogDaemon(cfg, mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0))
    daemon.start()
    try:
        cycle = daemon.run_once()
        assert cycle.overall_status == MonitoringStatus.INCONCLUSIVE
        assert cycle.outbox_available is True
        assert cycle.outbox_integrity_failures >= 1
    finally:
        daemon.stop()

    after = _snapshot_db(Path(cfg.db_path))
    assert after == before


def test_20d_outbox_missing_index_classified_inconclusive_without_recreating(
    tmp_path: Path,
) -> None:
    from desktop_worker.watchdog.outbox import OutboxStore

    cfg = _make_config(tmp_path)
    with StateStore(cfg.db_path) as store:
        OutboxStore(store)

    conn = sqlite3.connect(str(cfg.db_path))
    conn.execute("DROP INDEX idx_outbox_delivered;")
    conn.commit()
    conn.close()

    before = _snapshot_db(Path(cfg.db_path))

    daemon = WatchdogDaemon(cfg, mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0))
    daemon.start()
    try:
        cycle = daemon.run_once()
        assert cycle.overall_status == MonitoringStatus.INCONCLUSIVE
        assert cycle.outbox_available is True
        assert cycle.outbox_integrity_failures >= 1
    finally:
        daemon.stop()

    after = _snapshot_db(Path(cfg.db_path))
    assert after == before


# ---------------------------------------------------------------------------
# 21. Correction 4 — Do NOT Create Empty State Store on Invalid Path
# ---------------------------------------------------------------------------


def test_21a_nonexistent_state_store_returns_store_unavailable_without_creating_files(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing_dir = tmp_path / "does_not_exist_dir"
    missing_db = missing_dir / "watchdog.db"
    cfg = WatchdogConfig(
        worker_id="worker-missing-db",
        db_path=missing_db,
        poll_interval_sec=0.05,
        heartbeat_interval_sec=5.0,
        heartbeat_timeout_sec=30.0,
    )
    daemon = WatchdogDaemon(cfg, mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0))
    with caplog.at_level("ERROR"):
        daemon.start()
        try:
            cycle = daemon.run_once()
            assert cycle.overall_status == MonitoringStatus.STORE_UNAVAILABLE
            assert daemon.health_check().state_store_accessible is False
            assert any(
                getattr(r, "error_code", None) == "STATE_STORE_PATH_INVALID"
                for r in caplog.records
            )
        finally:
            daemon.stop()

    assert not missing_db.exists()
    assert not missing_dir.exists()


def test_21b_directory_as_state_store_returns_store_unavailable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    dir_path = tmp_path / "a_directory"
    dir_path.mkdir()
    cfg = WatchdogConfig(
        worker_id="worker-dir-db",
        db_path=dir_path,
        poll_interval_sec=0.05,
        heartbeat_interval_sec=5.0,
        heartbeat_timeout_sec=30.0,
    )
    daemon = WatchdogDaemon(cfg, mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0))
    with caplog.at_level("ERROR"):
        daemon.start()
        try:
            cycle = daemon.run_once()
            assert cycle.overall_status == MonitoringStatus.STORE_UNAVAILABLE
            assert daemon.health_check().state_store_accessible is False
            assert any(
                getattr(r, "error_code", None) == "STATE_STORE_PATH_INVALID"
                for r in caplog.records
            )
        finally:
            daemon.stop()


def test_21c_corrupted_sqlite_file_returns_store_unavailable_without_mutation(
    tmp_path: Path,
) -> None:
    corrupt_db = tmp_path / "corrupt.db"
    raw_bytes = b"not a valid sqlite database file at all 1234567890"
    corrupt_db.write_bytes(raw_bytes)

    cfg = WatchdogConfig(
        worker_id="worker-corrupt-db",
        db_path=corrupt_db,
        poll_interval_sec=0.05,
        heartbeat_interval_sec=5.0,
        heartbeat_timeout_sec=30.0,
    )
    daemon = WatchdogDaemon(cfg, mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0))
    daemon.start()
    try:
        cycle = daemon.run_once()
        assert cycle.overall_status == MonitoringStatus.STORE_UNAVAILABLE
        assert daemon.health_check().state_store_accessible is False
    finally:
        daemon.stop()

    assert corrupt_db.read_bytes() == raw_bytes


def test_21d_incompatible_schema_file_returns_store_unavailable_without_migration(
    tmp_path: Path,
) -> None:
    incompat_db = tmp_path / "incompat.db"
    conn = sqlite3.connect(str(incompat_db))
    conn.execute("CREATE TABLE other_table (id INTEGER PRIMARY KEY);")
    conn.commit()
    conn.close()

    daemon = WatchdogDaemon(
        WatchdogConfig(
            worker_id="worker-incompat-db",
            db_path=incompat_db,
            poll_interval_sec=0.05,
            heartbeat_interval_sec=5.0,
            heartbeat_timeout_sec=30.0,
        ),
        mutex_api=FakeMutexApi(wait_result=WAIT_OBJECT_0),
    )
    daemon.start()
    try:
        cycle = daemon.run_once()
        assert cycle.overall_status == MonitoringStatus.STORE_UNAVAILABLE
        assert daemon.health_check().state_store_accessible is False
    finally:
        daemon.stop()

    # Verify daemon did NOT create State Store tables in the incompatible DB
    conn = sqlite3.connect(str(incompat_db))
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    }
    conn.close()
    assert tables == {"other_table"}
