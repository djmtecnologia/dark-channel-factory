"""Mandatory tests for Sprint 2.7 - Task 6 (Final Correction):
Strict Launcher Identity (Fail-Closed), Watchdog Instance Binding,
Explicit Launcher Reauthorization, and Safe Concurrent IPC Shutdown.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from desktop_worker.watchdog.identity import (
    CanonicalExecutionIdentity,
    IdentityStatus,
    IdentityVerificationResult,
)
from desktop_worker.watchdog.job_handle_transfer import (
    JobHandleTransferClient,
    JobHandleTransferError,
    JobHandleTransferServer,
    PeerProcessIdentity,
    Win32HandleTransferApi,
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
from desktop_worker.watchdog.termination_evidence import TerminationVerificationResult
from desktop_worker.watchdog.watchdog_daemon import (
    WatchdogConfig,
    WatchdogDaemon,
    WatchdogIPCError,
)


# ---------------------------------------------------------------------------
# Helpers & Fakes
# ---------------------------------------------------------------------------


def _make_identity(
    *,
    execution_id: Optional[str] = None,
    attempt_id: Optional[str] = None,
    fencing_token: int = 10,
    worker_id: Optional[str] = None,
) -> CanonicalExecutionIdentity:
    uid = uuid.uuid4().hex[:8]
    return CanonicalExecutionIdentity(
        execution_id=execution_id or f"exec-{uid}",
        attempt_id=attempt_id or f"att-{uid}",
        job_id=f"job-{uid}",
        worker_id=worker_id or f"worker-strict-{uid}",
        tenant_id="tenant-strict",
        channel_id="channel-strict",
        browser_user_data_dir="C:\\Temp\\dcf_strict_profile",
        fencing_token=fencing_token,
        pid=43210,
        process_create_time=9988776655,
        job_object_name=f"Local\\DCF_JOB_{uid}",
    )


def _seed_store(
    tmp_path: Path, ident: CanonicalExecutionIdentity
) -> Tuple[Path, StateStore, OutboxStore]:
    db_path = tmp_path / f"strict_{ident.execution_id}.db"
    store = StateStore(db_path)
    outbox = OutboxStore(store)
    stale_hb = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    store.upsert_attempt(
        attempt_id=ident.attempt_id,
        job_id=ident.job_id,
        fencing_token=ident.fencing_token,
        hub_status=AttemptHubStatus.IN_PROGRESS,
        last_heartbeat_at_utc=stale_hb,
    )
    store.record_execution(
        ident,
        boot_time_utc="2026-11-19T10:00:00+00:00",
        status=ExecutionStatus.RUNNING,
    )
    return db_path, store, outbox


def _make_config(worker_id: str, db_path: Path, stop_timeout_sec: float = 2.0) -> WatchdogConfig:
    return WatchdogConfig(
        worker_id=worker_id,
        db_path=db_path,
        poll_interval_sec=1.0,
        heartbeat_interval_sec=5.0,
        heartbeat_timeout_sec=30.0,
        stop_timeout_sec=stop_timeout_sec,
    )


class _StubValidator:
    def __init__(self, ident: CanonicalExecutionIdentity) -> None:
        self._ident = ident

    def verify(self, persisted: Any, **kwargs: Any) -> IdentityVerificationResult:
        return IdentityVerificationResult(
            status=IdentityStatus.VALID,
            pid=self._ident.pid,
            expected_create_time=self._ident.process_create_time,
            actual_create_time=self._ident.process_create_time,
            reason="VALID",
        )


class _FakeWin32:
    def __init__(self, *, terminate_hook: Optional[Any] = None) -> None:
        self.live_handles: Dict[int, str] = {}
        self.closed_handles: List[int] = []
        self.terminate_calls: List[Tuple[int, int]] = []
        self._terminated: set[str] = set()
        self._terminate_hook = terminate_hook

    def add_handle(self, handle: int, kernel_obj: str) -> None:
        self.live_handles[handle] = kernel_obj

    def is_handle_valid(self, handle: int) -> bool:
        return handle in self.live_handles and handle not in self.closed_handles

    def compare_object_handles(self, handle_a: int, handle_b: int) -> bool:
        if not self.is_handle_valid(handle_a) or not self.is_handle_valid(handle_b):
            return False
        return self.live_handles[handle_a] == self.live_handles[handle_b]

    def close_handle(self, handle: int) -> bool:
        if handle in self.live_handles and handle not in self.closed_handles:
            self.closed_handles.append(handle)
            return True
        return False

    def query_basic_accounting(self, job_handle: int) -> Tuple[bool, Any, int]:
        from types import SimpleNamespace

        if not self.is_handle_valid(job_handle):
            return False, None, 6
        obj = self.live_handles[job_handle]
        active = 0 if obj in self._terminated else 1
        return True, SimpleNamespace(
            ActiveProcesses=active,
            TotalProcesses=1,
            TotalTerminatedProcesses=1 if active == 0 else 0,
        ), 0

    def query_process_id_list(self, job_handle: int, capacity: int = 64) -> Tuple[bool, Any, int]:
        from types import SimpleNamespace

        if not self.is_handle_valid(job_handle):
            return False, None, 6
        obj = self.live_handles[job_handle]
        pids = [] if obj in self._terminated else [43210]
        return True, SimpleNamespace(
            NumberOfAssignedProcesses=len(pids),
            NumberOfProcessIdsInList=len(pids),
            ProcessIdList=pids,
        ), 0

    def terminate_job_object(self, job_handle: int, exit_code: int = 1) -> bool:
        if self._terminate_hook is not None:
            self._terminate_hook(job_handle, exit_code)
        self.terminate_calls.append((job_handle, exit_code))
        obj = self.live_handles.get(job_handle)
        if obj is not None:
            self._terminated.add(obj)
        return True

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        return WAIT_OBJECT_0

    def get_exit_code_process(self, handle: int) -> Tuple[bool, int, int]:
        return True, 1, 0


def _assert_no_side_effects(
    win32: _FakeWin32, store: StateStore, outbox: OutboxStore, execution_id: str
) -> None:
    assert win32.terminate_calls == [], "TerminateJobObject must NOT be called"
    with store._lock:
        rows = store._require_conn().execute(
            "SELECT verification_result FROM termination_evidence WHERE execution_id = ?;",
            (execution_id,),
        ).fetchall()
    confirmed = [
        r["verification_result"]
        for r in rows
        if r["verification_result"] == TerminationVerificationResult.TREE_TERMINATED.value
    ]
    assert confirmed == [], "Must NOT persist false confirmed termination evidence"
    with store._lock:
        outbox_rows = store._require_conn().execute(
            "SELECT event_type FROM outbox_event WHERE execution_id = ?;",
            (execution_id,),
        ).fetchall()
    confirmed_events = [
        r["event_type"]
        for r in outbox_rows
        if r["event_type"] == "EXECUTION_TERMINATION_CONFIRMED"
    ]
    assert confirmed_events == [], "Must NOT enqueue false confirmed termination outbox event"


def _current_launcher_identity() -> Tuple[int, int]:
    pid = os.getpid()
    ct = Win32HandleTransferApi().get_current_process_create_time()
    assert ct is not None and ct > 0
    return pid, ct


# ---------------------------------------------------------------------------
# 1. Servidor não inicia sem expected_launcher_pid
# ---------------------------------------------------------------------------


def test_01_server_refuses_start_without_expected_launcher_pid(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="expected_launcher_pid is required"):
        JobHandleTransferServer(
            on_register=lambda *a: (True, "ok"),
            on_unregister=lambda _e: True,
            expected_launcher_pid=None,
            expected_launcher_create_time=123456789,
        )

    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeWin32()
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=None,
        expected_launcher_create_time=123456789,
    )
    try:
        with pytest.raises(WatchdogIPCError, match="expected_launcher_pid is required"):
            daemon.start()
        assert daemon.ipc_running is False
        assert daemon.is_running() is False
        _assert_no_side_effects(win32, store, outbox, ident.execution_id)
    finally:
        daemon.stop()
        store.close()


# ---------------------------------------------------------------------------
# 2. Servidor não inicia sem expected_launcher_create_time
# ---------------------------------------------------------------------------


def test_02_server_refuses_start_without_expected_launcher_create_time(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="expected_launcher_create_time is required"):
        JobHandleTransferServer(
            on_register=lambda *a: (True, "ok"),
            on_unregister=lambda _e: True,
            expected_launcher_pid=os.getpid(),
            expected_launcher_create_time=None,
        )

    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeWin32()
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=os.getpid(),
        expected_launcher_create_time=None,
    )
    try:
        with pytest.raises(WatchdogIPCError, match="expected_launcher_create_time is required"):
            daemon.start()
        assert daemon.ipc_running is False
        assert daemon.is_running() is False
        _assert_no_side_effects(win32, store, outbox, ident.execution_id)
    finally:
        daemon.stop()
        store.close()


# ---------------------------------------------------------------------------
# 3. Launcher com PID diferente é rejeitado
# ---------------------------------------------------------------------------


def test_03_different_launcher_pid_is_rejected(tmp_path: Path) -> None:
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeWin32()
    win32.add_handle(7001, "job-diff-pid")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))

    _, l_ct = _current_launcher_identity()
    # Pin server to a DIFFERENT PID than the connecting process
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=os.getpid() + 5000,
        expected_launcher_create_time=l_ct,
    )
    try:
        daemon.start()
        assert daemon.ipc_port is not None
        client = JobHandleTransferClient(port=daemon.ipc_port, auth_token=daemon.ipc_auth_token or "")
        resp = client._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 7001,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "target_watchdog_pid": os.getpid(),
                "target_daemon_instance_id": daemon.daemon_instance_id,
            }
        )
        assert resp["ok"] is False
        assert resp["error"] == "unauthorized_launcher_pid"
        assert engine.has_authorized_job_handle(ident.execution_id) is False

        out = daemon.request_safe_termination(TerminationContext(identity=ident, job_handle=7001))
        assert out.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
        _assert_no_side_effects(win32, store, outbox, ident.execution_id)
    finally:
        daemon.stop()
        store.close()


# ---------------------------------------------------------------------------
# 4. Launcher com mesmo PID, mas create_time diferente, é rejeitado
# ---------------------------------------------------------------------------


def test_04_same_pid_different_create_time_is_rejected(tmp_path: Path) -> None:
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeWin32()
    win32.add_handle(7002, "job-diff-ct")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))

    l_pid, l_ct = _current_launcher_identity()
    # Same PID, different create_time (simulates PID reuse)
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct + 999999,
    )
    try:
        daemon.start()
        assert daemon.ipc_port is not None
        client = JobHandleTransferClient(port=daemon.ipc_port, auth_token=daemon.ipc_auth_token or "")
        resp = client._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 7002,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "target_watchdog_pid": os.getpid(),
                "target_daemon_instance_id": daemon.daemon_instance_id,
            }
        )
        assert resp["ok"] is False
        assert resp["error"] == "launcher_pid_reused_or_mismatch"
        assert engine.has_authorized_job_handle(ident.execution_id) is False

        out = daemon.request_safe_termination(TerminationContext(identity=ident, job_handle=7002))
        assert out.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
        _assert_no_side_effects(win32, store, outbox, ident.execution_id)
    finally:
        daemon.stop()
        store.close()


# ---------------------------------------------------------------------------
# 5, 6, 7. PING, REGISTER_HANDLE e UNREGISTER_HANDLE rejeitam processo não autorizado
# ---------------------------------------------------------------------------


def test_05_ping_rejects_unauthorized_process(tmp_path: Path) -> None:
    _, l_ct = _current_launcher_identity()
    server = JobHandleTransferServer(
        on_register=lambda *a: (True, "ok"),
        on_unregister=lambda _e: True,
        auth_token="shared-secret-token",
        expected_launcher_pid=os.getpid() + 1234,
        expected_launcher_create_time=l_ct,
    )
    _, port = server.start()
    try:
        client = JobHandleTransferClient(port=port, auth_token="shared-secret-token")
        # Even with valid HMAC, unauthorized peer PID is rejected on PING
        with pytest.raises(JobHandleTransferError, match="unauthorized_launcher_pid"):
            client.ping_watchdog()
    finally:
        assert server.close() is True


def test_06_register_handle_rejects_unauthorized_process(tmp_path: Path) -> None:
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeWin32()
    win32.add_handle(7006, "job-unauth-reg")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))

    _, l_ct = _current_launcher_identity()
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=os.getpid() + 2345,
        expected_launcher_create_time=l_ct,
    )
    try:
        daemon.start()
        assert daemon.ipc_port is not None
        client = JobHandleTransferClient(port=daemon.ipc_port, auth_token=daemon.ipc_auth_token or "")
        resp = client._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 7006,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "target_watchdog_pid": os.getpid(),
                "target_daemon_instance_id": daemon.daemon_instance_id,
            }
        )
        assert resp["ok"] is False
        assert resp["error"] == "unauthorized_launcher_pid"
        assert engine.has_authorized_job_handle(ident.execution_id) is False
        _assert_no_side_effects(win32, store, outbox, ident.execution_id)
    finally:
        daemon.stop()
        store.close()


def test_07_unregister_handle_rejects_unauthorized_process(tmp_path: Path) -> None:
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeWin32()
    win32.add_handle(7007, "job-unauth-unreg")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))

    # Pre-register a legitimate handle
    engine.register_job_handle(
        ident.execution_id, 7007, owned=True, attempt_id=ident.attempt_id, fencing_token=ident.fencing_token
    )

    _, l_ct = _current_launcher_identity()
    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        expected_launcher_pid=os.getpid() + 3456,
        expected_launcher_create_time=l_ct,
    )
    try:
        daemon.start()
        assert daemon.ipc_port is not None
        client = JobHandleTransferClient(port=daemon.ipc_port, auth_token=daemon.ipc_auth_token or "")
        resp = client._send({"op": "UNREGISTER_HANDLE", "execution_id": ident.execution_id})
        assert resp["ok"] is False
        assert resp["error"] == "unauthorized_launcher_pid"
        # Legitimate handle MUST still be registered and open
        assert engine.has_authorized_job_handle(ident.execution_id) is True
        assert 7007 not in win32.closed_handles
        _assert_no_side_effects(win32, store, outbox, ident.execution_id)
    finally:
        daemon.stop()
        store.close()


# ---------------------------------------------------------------------------
# 8. Reinicialização do Launcher exige reautorização explícita
# ---------------------------------------------------------------------------


def test_08_launcher_restart_requires_explicit_reauthorization(tmp_path: Path) -> None:
    import subprocess
    import sys

    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeWin32()
    win32.add_handle(7008, "job-reauth")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))

    old_pid, old_ct = _current_launcher_identity()
    restarted_proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    api = Win32HandleTransferApi()
    restarted_pid = int(restarted_proc.pid)
    restarted_ct = api.get_process_create_time(restarted_pid)
    assert restarted_ct is not None and restarted_ct > 0

    # Simulate peer identity switching from old launcher to restarted launcher
    current_peer = {"identity": PeerProcessIdentity(pid=old_pid, process_create_time=old_ct)}

    def _server_factory(**kwargs: Any) -> JobHandleTransferServer:
        return JobHandleTransferServer(
            **kwargs,
            peer_identity_resolver=lambda _conn: current_peer["identity"],
        )

    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine,
        ipc_auth_token="reauth-secret-token",
        expected_launcher_pid=old_pid,
        expected_launcher_create_time=old_ct,
        ipc_server_factory=_server_factory,
    )
    try:
        daemon.start()
        assert daemon.ipc_port is not None
        client = JobHandleTransferClient(port=daemon.ipc_port, auth_token="reauth-secret-token")

        # Old launcher works
        ping1 = client.ping_watchdog()
        assert ping1.watchdog_pid == os.getpid()

        # Launcher restarts -> new PID & create_time
        current_peer["identity"] = PeerProcessIdentity(
            pid=restarted_pid, process_create_time=restarted_ct
        )

        # Without explicit reauthorization, restarted launcher is REJECTED
        with pytest.raises(JobHandleTransferError, match="unauthorized_launcher_pid"):
            client.ping_watchdog()

        # Reauthorization with wrong token is REJECTED
        with pytest.raises(JobHandleTransferError, match="invalid auth_token"):
            daemon.reauthorize_launcher(
                new_launcher_pid=restarted_pid,
                new_launcher_create_time=restarted_ct,
                auth_token="wrong-token",
            )

        # Explicit authenticated reauthorization succeeds (verified against Windows kernel)
        new_pid, new_ct = daemon.reauthorize_launcher(
            new_launcher_pid=restarted_pid,
            new_launcher_create_time=restarted_ct,
            auth_token="reauth-secret-token",
            reason="launcher_restart_test",
        )
        assert (new_pid, new_ct) == (restarted_pid, restarted_ct)

        # Restarted launcher is now accepted
        ping2 = client.ping_watchdog()
        assert ping2.daemon_instance_id == daemon.daemon_instance_id

        # Old launcher is now REJECTED
        current_peer["identity"] = PeerProcessIdentity(pid=old_pid, process_create_time=old_ct)
        with pytest.raises(JobHandleTransferError, match="unauthorized_launcher_pid"):
            client.ping_watchdog()
    finally:
        daemon.stop()
        store.close()
        restarted_proc.terminate()
        try:
            restarted_proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            restarted_proc.kill()


# ---------------------------------------------------------------------------
# 9. PING de Watchdog antigo não permite registro na nova instância
# ---------------------------------------------------------------------------


def test_09_old_watchdog_ping_cannot_register_on_new_instance(tmp_path: Path) -> None:
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeWin32()
    win32.add_handle(7009, "job-stale-ping")
    l_pid, l_ct = _current_launcher_identity()

    # Watchdog instance #1
    engine1 = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    daemon1 = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine1,
        ipc_auth_token="same-token-across-restart",
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct,
    )
    daemon1.start()
    assert daemon1.ipc_port is not None
    client1 = JobHandleTransferClient(port=daemon1.ipc_port, auth_token="same-token-across-restart")
    old_ping = client1.ping_watchdog()
    assert daemon1.stop() is True

    # Watchdog instance #2 (new daemon_instance_id)
    engine2 = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    daemon2 = WatchdogDaemon(
        _make_config(ident.worker_id, db_path),
        termination_engine=engine2,
        ipc_auth_token="same-token-across-restart",
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct,
    )
    try:
        daemon2.start()
        assert daemon2.ipc_port is not None
        assert daemon2.daemon_instance_id != old_ping.daemon_instance_id

        client2 = JobHandleTransferClient(port=daemon2.ipc_port, auth_token="same-token-across-restart")
        # Attempt to register on daemon2 using stale PING info from daemon1
        resp = client2._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 7009,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": l_pid,
                "target_watchdog_pid": old_ping.watchdog_pid,
                "target_watchdog_create_time": old_ping.watchdog_create_time,
                "target_daemon_instance_id": old_ping.daemon_instance_id,
            }
        )
        assert resp["ok"] is False
        assert resp["error"] == "watchdog_instance_mismatch"
        assert engine2.has_authorized_job_handle(ident.execution_id) is False

        out = daemon2.request_safe_termination(TerminationContext(identity=ident, job_handle=7009))
        assert out.error_code == "JOB_HANDLE_NOT_AUTHORIZED"
        _assert_no_side_effects(win32, store, outbox, ident.execution_id)
    finally:
        daemon2.stop()
        store.close()


# ---------------------------------------------------------------------------
# 10 & 11. Thread IPC que não encerra impede shutdown limpo e NÃO libera Mutex
# ---------------------------------------------------------------------------


def test_10_and_11_stuck_ipc_thread_prevents_clean_shutdown_and_keeps_mutex(tmp_path: Path) -> None:
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    win32 = _FakeWin32()
    win32.add_handle(7010, "job-stuck-ipc")
    l_pid, l_ct = _current_launcher_identity()

    in_handler = threading.Event()
    release_handler = threading.Event()

    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    orig_accept = engine.accept_transferred_job_handle

    def _blocking_accept(*args: Any, **kwargs: Any) -> Tuple[bool, str]:
        in_handler.set()
        release_handler.wait(timeout=5.0)
        return orig_accept(*args, **kwargs)

    engine.accept_transferred_job_handle = _blocking_accept  # type: ignore[method-assign]

    daemon = WatchdogDaemon(
        _make_config(ident.worker_id, db_path, stop_timeout_sec=0.3),
        termination_engine=engine,
        expected_launcher_pid=l_pid,
        expected_launcher_create_time=l_ct,
    )
    daemon.start()
    assert daemon.ipc_port is not None
    client = JobHandleTransferClient(port=daemon.ipc_port, auth_token=daemon.ipc_auth_token or "")
    ping = client.ping_watchdog()

    # Fire an IPC REGISTER_HANDLE request that blocks inside the server thread
    req_thread = threading.Thread(
        target=lambda: client._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 7010,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": l_pid,
                "target_watchdog_pid": ping.watchdog_pid,
                "target_watchdog_create_time": ping.watchdog_create_time,
                "target_daemon_instance_id": ping.daemon_instance_id,
            }
        ),
        daemon=True,
    )
    req_thread.start()
    assert in_handler.wait(timeout=3.0), "IPC handler did not start"

    try:
        # Attempt shutdown while IPC server thread is still processing request
        clean = daemon.stop(timeout_sec=0.2)
        # Test 10: MUST NOT declare clean shutdown
        assert clean is False
        # Test 11: MUST NOT release Mutex or close StateStore while IPC thread is alive
        assert daemon._mutex.is_owned is True
        assert daemon._store is not None
    finally:
        # Unblock handler and perform clean teardown
        release_handler.set()
        req_thread.join(timeout=3.0)
        assert daemon.stop(timeout_sec=2.0) is True
        assert daemon._mutex.is_owned is False
        store.close()


# ---------------------------------------------------------------------------
# 12. Shutdown não fecha handle utilizado por terminação ativa
# ---------------------------------------------------------------------------


def test_12_shutdown_does_not_close_handle_during_active_termination(tmp_path: Path) -> None:
    ident = _make_identity()
    db_path, store, outbox = _seed_store(tmp_path, ident)
    in_terminate = threading.Event()
    allow_terminate_finish = threading.Event()
    handle_valid_during_terminate: List[bool] = []

    def _hook(job_handle: int, _exit_code: int) -> None:
        in_terminate.set()
        # Wait until concurrent close() has been invoked
        allow_terminate_finish.wait(timeout=5.0)
        # Verify the handle is STILL OPEN while active termination is using it
        handle_valid_during_terminate.append(win32.is_handle_valid(job_handle))

    win32 = _FakeWin32(terminate_hook=_hook)
    win32.add_handle(7012, "job-active-term")
    engine = SafeTerminationEngine(store, outbox_store=outbox, win32=win32, identity_validator=_StubValidator(ident))
    engine.register_job_handle(
        ident.execution_id,
        7012,
        owned=True,
        attempt_id=ident.attempt_id,
        fencing_token=ident.fencing_token,
        source_pid=os.getpid(),
    )

    term_outcome: List[Any] = []

    def _run_term() -> None:
        out = engine.terminate_execution(
            TerminationContext(identity=ident, job_handle=7012, event_id="evt-active-12")
        )
        term_outcome.append(out)

    t = threading.Thread(target=_run_term, daemon=True)
    t.start()
    assert in_terminate.wait(timeout=3.0), "Termination did not reach TerminateJobObject"

    # Call engine.close() concurrently while termination is active
    engine.close()
    # Handle MUST NOT be closed yet!
    assert 7012 not in win32.closed_handles

    # Now let termination finish
    allow_terminate_finish.set()
    t.join(timeout=5.0)

    assert handle_valid_during_terminate == [True]
    assert len(term_outcome) == 1
    assert term_outcome[0].verification_result == TerminationVerificationResult.TREE_TERMINATED
    # After termination finishes, deferred close MUST have closed the handle deterministically
    assert 7012 in win32.closed_handles
    store.close()
