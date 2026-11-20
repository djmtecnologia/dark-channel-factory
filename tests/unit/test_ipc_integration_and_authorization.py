"""Mandatory IPC Integration & Authorization Binding tests (Sprint 2.7 - Task 6 Final Corrective Round).

Covers all 15 mandatory scenarios:
1.  WatchdogDaemon starts the IPC server during startup.
2.  WatchdogDaemon stops the IPC server deterministically during shutdown.
3.  IPC server startup failure prevents WatchdogDaemon from accepting termination requests.
4.  Legitimate Launcher registers handle via IPC and enables safe termination.
5.  Unauthenticated client cannot register handle (no TerminateJobObject, no evidence, no leak).
6.  Client spoofing source_pid is rejected (kernel peer PID verification).
7.  Replay of REGISTER_HANDLE request is rejected (nonce + timestamp protection).
8.  Handle bound to a different execution_id is rejected.
9.  Divergent attempt_id is rejected (cannot reuse handle across attempts).
10. Divergent fencing_token is rejected.
11. Watchdog process with reused PID does not receive unauthorized handle duplication.
12. Registration failure closes the duplicated handle (zero handle leak).
13. Restarted Watchdog blocks termination until re-authorized.
14. Concurrent revocation prevents use of closed handle and blocks TerminateJobObject.
15. Anti-TOCTOU revalidation immediately before TerminateJobObject continues working.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

import pytest

from desktop_worker.watchdog.identity import (
    CanonicalExecutionIdentity,
    IdentityStatus,
    IdentityVerificationResult,
)
from desktop_worker.watchdog.job_handle_transfer import (
    DUPLICATE_CLOSE_SOURCE,
    JOB_OBJECT_WATCHDOG_ACCESS,
    JobHandleTransferClient,
    JobHandleTransferError,
    JobHandleTransferServer,
    PeerProcessIdentity,
    compute_ipc_signature,
    duplicate_job_handle_to_process,
)
from desktop_worker.watchdog.job_object import (
    BasicAccountingInfo,
    ProcessIdListResult,
    WAIT_OBJECT_0,
)
from desktop_worker.watchdog.outbox import OutboxStore
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
    WatchdogIPCError,
)


# ---------------------------------------------------------------------------
# Deterministic Test Doubles
# ---------------------------------------------------------------------------


class _FakeHandleWin32:
    """Handle-aware Win32 double tracking live handles, closes, and TerminateJobObject calls."""

    def __init__(self) -> None:
        self.live_handles: Dict[int, str] = {}
        self.closed_handles: List[int] = []
        self.terminate_calls: List[Tuple[int, int]] = []
        self.terminated_objects: set[str] = set()
        self.on_before_terminate: Optional[Any] = None

    def add_handle(self, handle: int, kernel_object_id: str) -> None:
        self.live_handles[int(handle)] = kernel_object_id

    def is_handle_valid(self, handle: int) -> bool:
        return int(handle) in self.live_handles

    def compare_object_handles(self, h1: int, h2: int) -> bool:
        o1 = self.live_handles.get(int(h1))
        o2 = self.live_handles.get(int(h2))
        return o1 is not None and o2 is not None and o1 == o2

    def close_handle(self, handle: int) -> bool:
        h = int(handle)
        if h in self.live_handles:
            del self.live_handles[h]
            self.closed_handles.append(h)
            return True
        return False

    def query_basic_accounting(self, job_handle: int) -> Tuple[bool, Any, int]:
        from types import SimpleNamespace

        obj_id = self.live_handles.get(int(job_handle))
        if obj_id is None:
            return False, None, 6
        active = 0 if obj_id in self.terminated_objects else 1
        return True, SimpleNamespace(
            ActiveProcesses=active,
            TotalProcesses=1,
            TotalTerminatedProcesses=1 if active == 0 else 0,
        ), 0

    def query_process_id_list(self, job_handle: int, capacity: int = 64) -> Tuple[bool, Any, int]:
        from types import SimpleNamespace

        obj_id = self.live_handles.get(int(job_handle))
        if obj_id is None:
            return False, None, 6
        pids = [] if obj_id in self.terminated_objects else [4242]
        return True, SimpleNamespace(
            NumberOfAssignedProcesses=len(pids),
            NumberOfProcessIdsInList=len(pids),
            ProcessIdList=pids,
        ), 0

    def terminate_job_object(self, job_handle: int, exit_code: int) -> bool:
        if self.on_before_terminate is not None:
            self.on_before_terminate(job_handle, exit_code)
        self.terminate_calls.append((int(job_handle), int(exit_code)))
        obj_id = self.live_handles.get(int(job_handle))
        if obj_id is not None:
            self.terminated_objects.add(obj_id)
            return True
        return False

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        return WAIT_OBJECT_0

    def get_exit_code_process(self, handle: int) -> Tuple[bool, int, int]:
        return True, 1, 0


class _StubValidator:
    def __init__(self, ident: CanonicalExecutionIdentity, *, on_verify: Optional[Any] = None) -> None:
        self._ident = ident
        self._on_verify = on_verify
        self.calls = 0

    def verify(self, identity: CanonicalExecutionIdentity, *, job_handle: Optional[int] = None) -> IdentityVerificationResult:
        self.calls += 1
        if self._on_verify is not None:
            custom = self._on_verify(self.calls, identity, job_handle)
            if custom is not None:
                return custom
        return IdentityVerificationResult(
            status=IdentityStatus.VALID,
            pid=identity.pid,
            expected_create_time=identity.process_create_time,
            actual_create_time=identity.process_create_time,
            reason="VALID",
        )


class _FakeTransferApi:
    """Fake Win32HandleTransferApi for testing DuplicateHandle, PID reuse, and remote close."""

    def __init__(self, *, target_alive: bool = True, target_create_time: int = 100000) -> None:
        self.target_alive = target_alive
        self.target_create_time = target_create_time
        self.duplicated_handles: List[int] = []
        self.remote_closed_handles: List[int] = []
        self.opened_handles: List[int] = []
        self.closed_local_handles: List[int] = []
        self._next_handle = 8000

    def get_current_process_create_time(self) -> Optional[int]:
        return self.target_create_time

    def get_current_process(self) -> int:
        return 1

    def is_handle_valid(self, handle: int) -> bool:
        return handle > 0

    def open_process(self, desired_access: int, inherit: bool, pid: int) -> Tuple[int, int]:
        h = 9000 + len(self.opened_handles)
        self.opened_handles.append(h)
        return h, 0

    def close_handle(self, handle: int) -> bool:
        self.closed_local_handles.append(handle)
        return True

    def is_process_alive(self, process_handle: int) -> bool:
        return self.target_alive

    def get_process_create_time_by_handle(self, process_handle: int) -> Optional[int]:
        return self.target_create_time

    def duplicate_handle(
        self,
        source_process: int,
        source_handle: int,
        target_process: int,
        *,
        desired_access: int = JOB_OBJECT_WATCHDOG_ACCESS,
        inherit: bool = False,
        options: int = 0,
    ) -> Tuple[bool, int, int]:
        if options == DUPLICATE_CLOSE_SOURCE:
            self.remote_closed_handles.append(int(source_handle))
            return True, 0, 0
        self._next_handle += 1
        self.duplicated_handles.append(self._next_handle)
        return True, self._next_handle, 0


def _make_identity(
    *,
    execution_id: str = "exec-ipc-1",
    attempt_id: str = "att-ipc-1",
    fencing_token: int = 10,
    worker_id: Optional[str] = None,
) -> CanonicalExecutionIdentity:
    return CanonicalExecutionIdentity(
        execution_id=execution_id,
        attempt_id=attempt_id,
        job_id="job-ipc-1",
        tenant_id="tenant-1",
        channel_id="chan-1",
        worker_id=worker_id or f"worker-ipc-{uuid.uuid4().hex[:8]}",
        fencing_token=fencing_token,
        pid=4242,
        process_create_time=133000000000000000,
        job_object_name=f"dcf-job-{execution_id}",
        browser_user_data_dir="C:/tmp/prof-ipc",
    )


def _seed_store(tmp_path: Path, ident: CanonicalExecutionIdentity, db_name: str = "state.db") -> Tuple[Path, StateStore, OutboxStore]:
    db_path = tmp_path / db_name
    store = StateStore(db_path)
    outbox = OutboxStore(store)
    store.upsert_attempt(
        attempt_id=ident.attempt_id,
        job_id=ident.job_id,
        fencing_token=ident.fencing_token,
        hub_status=AttemptHubStatus.IN_PROGRESS,
        last_heartbeat_at_utc=(datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(),
    )
    store.record_execution(
        ident,
        boot_time_utc="2026-11-19T10:00:00+00:00",
        status=ExecutionStatus.RUNNING,
    )
    return db_path, store, outbox


def _make_config(worker_id: str, db_path: Path) -> WatchdogConfig:
    return WatchdogConfig(
        worker_id=worker_id,
        db_path=db_path,
        poll_interval_sec=1.0,
        heartbeat_interval_sec=5.0,
        heartbeat_timeout_sec=30.0,
    )


def _assert_no_termination_side_effects(
    win32: _FakeHandleWin32,
    store: StateStore,
    outbox: OutboxStore,
    execution_id: str,
) -> None:
    assert win32.terminate_calls == [], f"TerminateJobObject must NOT be called: {win32.terminate_calls}"
    ev = store.get_evidence(execution_id)
    if ev is not None:
        assert ev.verification_result != TerminationVerificationResult.TREE_TERMINATED.value
    pending = outbox.get_pending_events()
    for evt in pending:
        assert evt.event_type != TerminationOutboxEventType.TERMINATION_CONFIRMED.value


def _raw_ipc_send(port: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    with socket.create_connection(("127.0.0.1", port), timeout=3.0) as sock:
        sock.settimeout(3.0)
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        raw = b""
        while not raw.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# 15 Mandatory Tests
# ---------------------------------------------------------------------------


def _current_launcher_identity() -> tuple[int, int]:
    from desktop_worker.watchdog.job_handle_transfer import Win32HandleTransferApi

    pid = os.getpid()
    ct = Win32HandleTransferApi().get_current_process_create_time()
    assert ct is not None and ct > 0
    return pid, ct


def test_01_watchdog_daemon_starts_ipc_server(tmp_path: Path) -> None:
    """Test 1: WatchdogDaemon starts JobHandleTransferServer during start()."""
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    l_pid, l_ct = _current_launcher_identity()
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct,
    )
    try:
        assert daemon.ipc_running is False
        daemon.start()
        assert daemon.ipc_running is True
        assert daemon.ipc_port is not None and daemon.ipc_port > 0
        hc = daemon.health_check()
        assert hc.ipc_running is True
        assert hc.ipc_port == daemon.ipc_port

        # Verify live PING over the daemon's IPC server
        client = JobHandleTransferClient(port=daemon.ipc_port, auth_token=daemon.ipc_auth_token or "")
        ping = client.ping_watchdog()
        assert ping.watchdog_pid == os.getpid()
        assert ping.daemon_instance_id == daemon.daemon_instance_id
    finally:
        daemon.stop()
        store.close()


def test_02_watchdog_daemon_stops_ipc_server_cleanly(tmp_path: Path) -> None:
    """Test 2: WatchdogDaemon stops IPC server deterministically during stop()."""
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    l_pid, l_ct = _current_launcher_identity()
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct,
    )
    daemon.start()
    port = daemon.ipc_port
    assert port is not None
    assert daemon.ipc_running is True

    stopped = daemon.stop()
    assert stopped is True
    assert daemon.ipc_running is False
    assert daemon.ipc_port is None
    assert daemon.health_check().ipc_running is False

    # Port must no longer accept connections
    with pytest.raises(OSError):
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            pass
    store.close()


def test_03_ipc_server_failure_blocks_termination(tmp_path: Path) -> None:
    """Test 3: Failure starting IPC server prevents WatchdogDaemon from accepting termination."""
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    win32.add_handle(5001, "obj-1")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    engine.register_job_handle(ident.execution_id, 5001, attempt_id=ident.attempt_id, fencing_token=ident.fencing_token)

    def _failing_server_factory(**kwargs: Any) -> JobHandleTransferServer:
        raise OSError("Simulated bind failure on IPC port")

    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        ipc_server_factory=_failing_server_factory,
    )
    with pytest.raises(WatchdogIPCError, match="Failed to start JobHandleTransferServer"):
        daemon.start()

    assert daemon.ipc_running is False
    assert daemon.ipc_error is not None
    assert daemon.is_running() is False

    # Even if someone attempts request_safe_termination, it is blocked
    with pytest.raises(Exception):
        daemon.request_safe_termination(TerminationContext(identity=ident, job_handle=5001))

    _assert_no_termination_side_effects(win32, store, outbox, ident.execution_id)
    store.close()


def test_04_legitimate_launcher_registers_handle_and_terminates(tmp_path: Path) -> None:
    """Test 4: Legitimate authenticated Launcher registers handle via WatchdogDaemon IPC."""
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    win32.add_handle(6001, "kernel-job-legit")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))

    l_pid, l_ct = _current_launcher_identity()
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct,
    )
    try:
        daemon.start()
        assert daemon.ipc_port is not None
        client = JobHandleTransferClient(port=daemon.ipc_port, auth_token=daemon.ipc_auth_token or "")
        ping = client.ping_watchdog()
        resp = client._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 6001,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": os.getpid(),
                "target_watchdog_pid": ping.watchdog_pid,
                "target_watchdog_create_time": ping.watchdog_create_time,
                "target_daemon_instance_id": ping.daemon_instance_id,
            }
        )
        assert resp["ok"] is True
        entry = engine.get_authorized_job_entry(ident.execution_id)
        assert entry is not None
        assert entry.attempt_id == ident.attempt_id
        assert entry.fencing_token == ident.fencing_token
        assert entry.source_pid == os.getpid()

        outcome = daemon.request_safe_termination(
            TerminationContext(identity=ident, job_handle=6001, event_id="evt-legit-1")
        )
        assert outcome.verification_result == TerminationVerificationResult.TREE_TERMINATED
        assert outcome.fully_completed is True
        assert len(win32.terminate_calls) == 1
    finally:
        daemon.stop()
        store.close()
    # Owned handle closed deterministically on daemon.stop()
    assert 6001 in win32.closed_handles


def test_05_unauthenticated_client_cannot_register_handle(tmp_path: Path) -> None:
    """Test 5: Client without valid HMAC authentication is rejected without registering or leaking."""
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    win32.add_handle(6002, "kernel-job-unauth")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    l_pid, l_ct = _current_launcher_identity()
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct,
    )
    try:
        daemon.start()
        assert daemon.ipc_port is not None

        # 5a. No signature at all
        resp_no_sig = _raw_ipc_send(
            daemon.ipc_port,
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 6002,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "nonce": "a" * 32,
                "timestamp": time.time(),
            },
        )
        assert resp_no_sig["ok"] is False
        assert resp_no_sig["error"] == "unauthorized"

        # 5b. Wrong auth token
        bad_client = JobHandleTransferClient(port=daemon.ipc_port, auth_token="wrong-secret-token")
        resp_wrong = bad_client._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 6002,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
            }
        )
        assert resp_wrong["ok"] is False
        assert resp_wrong["error"] == "unauthorized"

        assert engine.has_authorized_job_handle(ident.execution_id) is False
        out = daemon.request_safe_termination(TerminationContext(identity=ident, job_handle=6002))
        assert out.verification_result == TerminationVerificationResult.INCONCLUSIVE
        assert out.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
        _assert_no_termination_side_effects(win32, store, outbox, ident.execution_id)
    finally:
        daemon.stop()
        store.close()


def test_06_spoofed_source_pid_is_rejected(tmp_path: Path) -> None:
    """Test 6: Client that spoofs source_pid != kernel-verified peer PID is rejected."""
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    win32.add_handle(6003, "kernel-job-spoof")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    l_pid, l_ct = _current_launcher_identity()
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct,
    )
    try:
        daemon.start()
        assert daemon.ipc_port is not None
        client = JobHandleTransferClient(port=daemon.ipc_port, auth_token=daemon.ipc_auth_token or "")
        spoofed_pid = os.getpid() + 9999
        resp = client._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 6003,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": spoofed_pid,
            }
        )
        assert resp["ok"] is False
        assert resp["error"] == "spoofed_source_pid"
        assert engine.has_authorized_job_handle(ident.execution_id) is False

        out = daemon.request_safe_termination(TerminationContext(identity=ident, job_handle=6003))
        assert out.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
        _assert_no_termination_side_effects(win32, store, outbox, ident.execution_id)
    finally:
        daemon.stop()
        store.close()


def test_07_replay_of_register_handle_is_rejected(tmp_path: Path) -> None:
    """Test 7: Replaying a captured signed REGISTER_HANDLE request is rejected."""
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    win32.add_handle(6004, "kernel-job-replay")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    l_pid, l_ct = _current_launcher_identity()
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct,
    )
    try:
        daemon.start()
        assert daemon.ipc_port is not None
        client = JobHandleTransferClient(port=daemon.ipc_port, auth_token=daemon.ipc_auth_token or "")
        ping = client.ping_watchdog()
        signed_req = client._build_signed_request(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 6004,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": os.getpid(),
                "target_watchdog_pid": ping.watchdog_pid,
                "target_watchdog_create_time": ping.watchdog_create_time,
                "target_daemon_instance_id": ping.daemon_instance_id,
            }
        )
        # First submission succeeds
        resp1 = client._send_raw(signed_req)
        assert resp1["ok"] is True

        # Unregister so we can verify replay does NOT re-register it
        assert client.unregister(ident.execution_id) is True
        assert engine.has_authorized_job_handle(ident.execution_id) is False

        # Replay exact same signed request -> MUST BE REJECTED
        resp2 = client._send_raw(signed_req)
        assert resp2["ok"] is False
        assert resp2["error"] == "replay_detected"
        assert engine.has_authorized_job_handle(ident.execution_id) is False
        _assert_no_termination_side_effects(win32, store, outbox, ident.execution_id)
    finally:
        daemon.stop()
        store.close()


def test_08_handle_from_different_execution_is_rejected(tmp_path: Path) -> None:
    """Test 8: Handle registered for execution A cannot be used to terminate execution B."""
    ident_a = _make_identity(execution_id="exec-A", attempt_id="att-A", fencing_token=1)
    ident_b = _make_identity(execution_id="exec-B", attempt_id="att-B", fencing_token=2, worker_id=ident_a.worker_id)
    db_path, store, outbox = _seed_store(tmp_path, ident_a)
    store.upsert_attempt(
        attempt_id=ident_b.attempt_id,
        job_id=ident_b.job_id,
        fencing_token=ident_b.fencing_token,
        hub_status=AttemptHubStatus.IN_PROGRESS,
        last_heartbeat_at_utc=(datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(),
    )
    store.record_execution(ident_b, boot_time_utc="2026-11-19T10:00:00+00:00", status=ExecutionStatus.RUNNING)

    win32 = _FakeHandleWin32()
    win32.add_handle(6010, "kernel-job-A")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident_b))
    ok, _ = engine.accept_transferred_job_handle(
        ident_a.execution_id,
        6010,
        attempt_id=ident_a.attempt_id,
        fencing_token=ident_a.fencing_token,
        source_pid=os.getpid(),
    )
    assert ok is True

    # Attempt to terminate exec-B using exec-A's handle -> BLOCKED
    out = engine.terminate_execution(TerminationContext(identity=ident_b, job_handle=6010))
    assert out.classification == OrphanClassification.INCONCLUSIVE
    assert out.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
    _assert_no_termination_side_effects(win32, store, outbox, ident_b.execution_id)
    engine.close()
    assert 6010 in win32.closed_handles
    store.close()


def test_09_divergent_attempt_id_is_rejected(tmp_path: Path) -> None:
    """Test 9: Handle authorized for attempt 1 cannot be reused for attempt 2 of the same execution."""
    ident = _make_identity(execution_id="exec-att", attempt_id="att-2", fencing_token=10)
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    win32.add_handle(6011, "kernel-job-att")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))

    # Authorize handle for att-1 (older attempt of same execution)
    ok, _ = engine.accept_transferred_job_handle(
        ident.execution_id,
        6011,
        attempt_id="att-1",
        fencing_token=ident.fencing_token,
        source_pid=os.getpid(),
    )
    assert ok is True

    out = engine.terminate_execution(TerminationContext(identity=ident, job_handle=6011))
    assert out.classification == OrphanClassification.IDENTITY_MISMATCH
    assert out.error_code == "AUTHORIZED_ATTEMPT_MISMATCH"
    _assert_no_termination_side_effects(win32, store, outbox, ident.execution_id)
    engine.close()
    assert 6011 in win32.closed_handles
    store.close()


def test_10_divergent_fencing_token_is_rejected(tmp_path: Path) -> None:
    """Test 10: Handle authorized with fencing_token=9 cannot terminate execution with fencing_token=10."""
    ident = _make_identity(execution_id="exec-ft", attempt_id="att-ft", fencing_token=10)
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    win32.add_handle(6012, "kernel-job-ft")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))

    ok, _ = engine.accept_transferred_job_handle(
        ident.execution_id,
        6012,
        attempt_id=ident.attempt_id,
        fencing_token=9,
        source_pid=os.getpid(),
    )
    assert ok is True

    out = engine.terminate_execution(TerminationContext(identity=ident, job_handle=6012))
    assert out.classification == OrphanClassification.IDENTITY_MISMATCH
    assert out.error_code == "AUTHORIZED_FENCING_TOKEN_MISMATCH"
    _assert_no_termination_side_effects(win32, store, outbox, ident.execution_id)
    engine.close()
    assert 6012 in win32.closed_handles
    store.close()


def test_11_watchdog_pid_reuse_blocks_handle_duplication() -> None:
    """Test 11: Watchdog PID reuse (different process_create_time) blocks DuplicateHandle."""
    fake_api = _FakeTransferApi(target_alive=True, target_create_time=222222)
    with pytest.raises(JobHandleTransferError, match="possible PID reuse"):
        duplicate_job_handle_to_process(
            source_job_handle=500,
            target_pid=9999,
            expected_target_create_time=111111,  # Original Watchdog create_time
            api=fake_api,  # type: ignore[arg-type]
        )
    # No handle duplicated into the reused PID, and intermediate process handle closed
    assert fake_api.duplicated_handles == []
    assert len(fake_api.opened_handles) == len(fake_api.closed_local_handles)


def test_12_registration_failure_closes_duplicated_handle(tmp_path: Path) -> None:
    """Test 12: If REGISTER_HANDLE fails after DuplicateHandle, the duplicated handle is closed."""
    fake_api = _FakeTransferApi(target_alive=True, target_create_time=555555)
    # Server that rejects REGISTER_HANDLE without closing handle itself
    server = JobHandleTransferServer(
        on_register=lambda *args: (False, "rejected_for_test"),
        on_unregister=lambda _eid: False,
        auth_token="tok-cleanup-test",
        expected_launcher_pid=os.getpid(),
        expected_launcher_create_time=1,
        peer_identity_resolver=lambda _conn: PeerProcessIdentity(pid=os.getpid(), process_create_time=1),
        api=fake_api,  # type: ignore[arg-type]
    )
    server._watchdog_create_time = 555555
    _, port = server.start()
    try:
        # Case A: Server returns ok=False with handle_closed_by_server=True -> server handled it
        # Case B: Transport error during REGISTER_HANDLE -> client calls close_remote_handle
        client = JobHandleTransferClient(
            port=port,
            auth_token="tok-cleanup-test",
            api=fake_api,  # type: ignore[arg-type]
        )
        # Simulate transport failure on REGISTER_HANDLE after PING succeeds
        orig_send = client._send

        def _fail_on_register(payload: Dict[str, Any]) -> Dict[str, Any]:
            if payload.get("op") == "REGISTER_HANDLE":
                raise OSError("Simulated connection reset before REGISTER_HANDLE")
            return orig_send(payload)

        client._send = _fail_on_register  # type: ignore[method-assign]
        with pytest.raises(JobHandleTransferError, match="remote handle cleaned up"):
            client.transfer_and_register(
                execution_id="exec-leak",
                source_job_handle=777,
                attempt_id="att-leak",
                fencing_token=1,
            )
        assert len(fake_api.duplicated_handles) == 1
        assert fake_api.remote_closed_handles == fake_api.duplicated_handles
    finally:
        server.close()


def test_13_restarted_watchdog_blocks_until_reauthorized(tmp_path: Path) -> None:
    """Test 13: After WatchdogDaemon restart, new instance is fail-closed until re-authorized."""
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    win32.add_handle(6020, "kernel-job-restart")

    # Watchdog #1
    l_pid, l_ct = _current_launcher_identity()
    engine1 = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    daemon1 = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine1,
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct,
    )
    daemon1.start()
    ok, _ = engine1.accept_transferred_job_handle(
        ident.execution_id, 6020, attempt_id=ident.attempt_id, fencing_token=ident.fencing_token, source_pid=os.getpid()
    )
    assert ok is True
    assert daemon1.stop() is True
    assert 6020 in win32.closed_handles

    # Watchdog #2 (fresh instance, empty authorization registry)
    win32.add_handle(6021, "kernel-job-restart")
    engine2 = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    daemon2 = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine2,
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct,
    )
    try:
        daemon2.start()
        blocked = daemon2.request_safe_termination(TerminationContext(identity=ident, job_handle=6021))
        assert blocked.verification_result == TerminationVerificationResult.INCONCLUSIVE
        assert blocked.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
        _assert_no_termination_side_effects(win32, store, outbox, ident.execution_id)

        # Re-authorize via IPC on Watchdog #2
        assert daemon2.ipc_port is not None
        client = JobHandleTransferClient(port=daemon2.ipc_port, auth_token=daemon2.ipc_auth_token or "")
        ping2 = client.ping_watchdog()
        resp = client._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 6021,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": os.getpid(),
                "target_watchdog_pid": ping2.watchdog_pid,
                "target_watchdog_create_time": ping2.watchdog_create_time,
                "target_daemon_instance_id": ping2.daemon_instance_id,
            }
        )
        assert resp["ok"] is True
        ok_out = daemon2.request_safe_termination(
            TerminationContext(identity=ident, job_handle=6021, event_id="evt-reauth")
        )
        assert ok_out.verification_result == TerminationVerificationResult.TREE_TERMINATED
        assert ok_out.fully_completed is True
    finally:
        daemon2.stop()
        store.close()
    assert 6021 in win32.closed_handles


def test_14_concurrent_revocation_blocks_termination_and_defers_close(tmp_path: Path) -> None:
    """Test 14: Concurrent unregister during terminate_execution blocks TerminateJobObject and safely defers CloseHandle."""
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    win32.add_handle(6030, "kernel-job-concurrent")

    engine_holder: List[SafeTerminationEngine] = []
    closed_during_verify: List[bool] = []

    def _revoke_mid_flight(call_num: int, _identity: CanonicalExecutionIdentity, _jh: Optional[int]) -> None:
        if call_num == 1:
            # Concurrently revoke authorization between Step 9 and Step 11/6b
            engine_holder[0].unregister_job_handle(ident.execution_id, close_if_owned=True)
            # Handle MUST NOT be closed yet while terminate_execution is still active
            closed_during_verify.append(6030 in win32.closed_handles)
        return None

    engine = SafeTerminationEngine(
        store,
        outbox_store=outbox,
        win32=win32,
        identity_validator=_StubValidator(ident, on_verify=_revoke_mid_flight),
    )
    engine_holder.append(engine)
    ok, _ = engine.accept_transferred_job_handle(
        ident.execution_id, 6030, attempt_id=ident.attempt_id, fencing_token=ident.fencing_token, source_pid=os.getpid()
    )
    assert ok is True

    out = engine.terminate_execution(TerminationContext(identity=ident, job_handle=6030))
    assert out.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert out.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
    assert closed_during_verify == [False], "Handle must not be closed mid-flight while execution is active"
    assert 6030 in win32.closed_handles, "Deferred handle must be closed deterministically when terminate_execution exits"
    _assert_no_termination_side_effects(win32, store, outbox, ident.execution_id)
    store.close()


def test_15_anti_toctou_revalidation_blocks_pid_reuse_before_terminate(tmp_path: Path) -> None:
    """Test 15: Anti-TOCTOU revalidation immediately before TerminateJobObject blocks PID reuse."""
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeHandleWin32()
    win32.add_handle(6040, "kernel-job-toctou")

    def _toctou_pid_reuse(call_num: int, identity: CanonicalExecutionIdentity, _jh: Optional[int]) -> Optional[IdentityVerificationResult]:
        if call_num == 2:
            return IdentityVerificationResult(
                status=IdentityStatus.PID_REUSED,
                pid=identity.pid,
                expected_create_time=identity.process_create_time,
                actual_create_time=identity.process_create_time + 999999,
                reason="PID reused immediately before TerminateJobObject",
            )
        return None

    engine = SafeTerminationEngine(
        store,
        outbox_store=outbox,
        win32=win32,
        identity_validator=_StubValidator(ident, on_verify=_toctou_pid_reuse),
    )
    ok, _ = engine.accept_transferred_job_handle(
        ident.execution_id, 6040, attempt_id=ident.attempt_id, fencing_token=ident.fencing_token, source_pid=os.getpid()
    )
    assert ok is True

    out = engine.terminate_execution(TerminationContext(identity=ident, job_handle=6040))
    assert out.classification == OrphanClassification.IDENTITY_MISMATCH
    assert out.verification_result == TerminationVerificationResult.INCONCLUSIVE
    assert out.error_code == "IDENTITY_RECHECK_FAILED"
    _assert_no_termination_side_effects(win32, store, outbox, ident.execution_id)
    engine.close()
    assert 6040 in win32.closed_handles
    store.close()
