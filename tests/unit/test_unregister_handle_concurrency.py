"""UNREGISTER_HANDLE concurrency and fail-closed identity tests (Sprint 2.7 - Task 6 Final Fix).

Validates that ``UNREGISTER_HANDLE`` follows the exact same ``_identity_lock``
synchronization discipline as ``REGISTER_HANDLE`` and ``reauthorize_launcher()``:
- Re-validates peer identity inside ``_identity_lock`` and invokes
  ``on_unregister`` under the same lock.
- Prevents a revoked Launcher from completing an in-flight ``UNREGISTER_HANDLE``
  after ``reauthorize_launcher()`` returns.
- Rejects mismatched ``create_time`` (PID reuse) and unauthorized callers.
- Prevents unauthorized ``UNREGISTER_HANDLE`` from revoking another attempt's
  authorized Job Object handle in ``SafeTerminationEngine``.
- Ensures zero spurious ``TerminateJobObject`` calls and zero false confirmed
  termination evidence.
- Ensures zero deadlocks during concurrent ``UNREGISTER_HANDLE``,
  ``reauthorize_launcher()``, and server/engine shutdown.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from desktop_worker.watchdog.job_handle_transfer import (
    JobHandleTransferServer,
    PeerProcessIdentity,
    Win32HandleTransferApi,
    compute_ipc_signature,
)
from desktop_worker.watchdog.outbox import OutboxStore
from desktop_worker.watchdog.state_store import StateStore
from desktop_worker.watchdog.termination_engine import SafeTerminationEngine


def _current_identity() -> Tuple[int, int]:
    api = Win32HandleTransferApi()
    pid = os.getpid()
    ct = api.get_current_process_create_time()
    assert ct is not None and ct > 0
    return pid, int(ct)


def _spawn_live_process() -> Tuple[subprocess.Popen[bytes], int, int]:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    api = Win32HandleTransferApi()
    ct = api.get_process_create_time(int(proc.pid))
    assert ct is not None and ct > 0
    return proc, int(proc.pid), int(ct)


def _terminate_proc(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)


def _signed_unregister_req(
    server: JobHandleTransferServer,
    *,
    execution_id: str,
    source_pid: Optional[int] = None,
) -> Dict[str, Any]:
    req: Dict[str, Any] = {
        "op": "UNREGISTER_HANDLE",
        "execution_id": execution_id,
        "nonce": secrets.token_hex(16),
        "timestamp": time.time(),
    }
    if source_pid is not None:
        req["source_pid"] = source_pid
    req["signature"] = compute_ipc_signature(server.auth_token, req)
    return req


class _SpyTerminationApi:
    """Minimal Win32 API spy that tracks TerminateJobObject and handle validity."""

    def __init__(self) -> None:
        self.terminate_job_calls: List[Tuple[int, int]] = []
        self.closed_handles: List[int] = []
        self._valid_handles = {101, 202, 303}

    def terminate_job_object(self, job_handle: int, exit_code: int) -> Tuple[bool, int]:
        self.terminate_job_calls.append((job_handle, exit_code))
        return True, 0

    def is_handle_valid(self, handle: int) -> bool:
        return int(handle) in self._valid_handles

    def compare_object_handles(self, handle_a: int, handle_b: int) -> bool:
        return int(handle_a) == int(handle_b) and int(handle_a) in self._valid_handles

    def close_handle(self, handle: int) -> bool:
        self.closed_handles.append(int(handle))
        self._valid_handles.discard(int(handle))
        return True


# ---------------------------------------------------------------------------
# 1. UNREGISTER iniciado antes da reautorização e concluído antes dela
# ---------------------------------------------------------------------------


def test_unregister_started_and_completed_before_reauthorization() -> None:
    old_pid, old_ct = _current_identity()
    proc, new_pid, new_ct = _spawn_live_process()
    unregistered: List[str] = []

    server = JobHandleTransferServer(
        on_register=lambda *_args: (True, "ok"),
        on_unregister=lambda exec_id: (unregistered.append(exec_id) or True),
        auth_token="unreg-before-reauth-token",
        expected_launcher_pid=old_pid,
        expected_launcher_create_time=old_ct,
    )
    try:
        req = _signed_unregister_req(server, execution_id="exec-before-reauth", source_pid=old_pid)
        resp = server._handle_request(
            req,
            peer_identity=PeerProcessIdentity(pid=old_pid, process_create_time=old_ct),
        )
        assert resp == {"ok": True, "removed": True}
        assert unregistered == ["exec-before-reauth"]

        res = server.reauthorize_launcher(
            new_launcher_pid=new_pid,
            new_launcher_create_time=new_ct,
            auth_token=server.auth_token,
        )
        assert res == (new_pid, new_ct)
    finally:
        _terminate_proc(proc)


# ---------------------------------------------------------------------------
# 2. UNREGISTER iniciado pelo Launcher antigo após a reautorização: rejeitado
# ---------------------------------------------------------------------------


def test_unregister_from_old_launcher_after_reauthorization_rejected() -> None:
    old_pid, old_ct = _current_identity()
    proc, new_pid, new_ct = _spawn_live_process()
    unregistered: List[str] = []

    server = JobHandleTransferServer(
        on_register=lambda *_args: (True, "ok"),
        on_unregister=lambda exec_id: (unregistered.append(exec_id) or True),
        auth_token="unreg-after-reauth-token",
        expected_launcher_pid=old_pid,
        expected_launcher_create_time=old_ct,
    )
    try:
        server.reauthorize_launcher(
            new_launcher_pid=new_pid,
            new_launcher_create_time=new_ct,
            auth_token=server.auth_token,
        )

        req = _signed_unregister_req(server, execution_id="exec-old-launcher", source_pid=old_pid)
        resp = server._handle_request(
            req,
            peer_identity=PeerProcessIdentity(pid=old_pid, process_create_time=old_ct),
        )
        assert resp["ok"] is False
        assert resp["error"] == "unauthorized_launcher_pid"
        assert unregistered == []
    finally:
        _terminate_proc(proc)


# ---------------------------------------------------------------------------
# 3. UNREGISTER concorrente com reauthorize_launcher: serialização correta
# ---------------------------------------------------------------------------


def test_unregister_concurrent_with_reauthorize_serializes_in_flight_callback() -> None:
    """When UNREGISTER enters _identity_lock first, reauthorize_launcher MUST wait
    until on_unregister finishes before returning."""
    old_pid, old_ct = _current_identity()
    proc, new_pid, new_ct = _spawn_live_process()

    events: List[str] = []
    events_lock = threading.Lock()
    in_unregister = threading.Event()
    release_unregister = threading.Event()

    def _slow_on_unregister(exec_id: str) -> bool:
        with events_lock:
            events.append(f"unregister_start:{exec_id}")
        in_unregister.set()
        assert release_unregister.wait(timeout=5.0)
        with events_lock:
            events.append(f"unregister_end:{exec_id}")
        return True

    server = JobHandleTransferServer(
        on_register=lambda *_args: (True, "ok"),
        on_unregister=_slow_on_unregister,
        auth_token="unreg-concurrent-token",
        expected_launcher_pid=old_pid,
        expected_launcher_create_time=old_ct,
    )

    unreg_resp: Dict[str, Any] = {}
    reauth_result: List[Tuple[int, int]] = []

    def _run_unregister() -> None:
        req = _signed_unregister_req(server, execution_id="exec-inflight", source_pid=old_pid)
        unreg_resp.update(
            server._handle_request(
                req,
                peer_identity=PeerProcessIdentity(pid=old_pid, process_create_time=old_ct),
            )
        )

    def _run_reauth() -> None:
        assert in_unregister.wait(timeout=5.0)
        res = server.reauthorize_launcher(
            new_launcher_pid=new_pid,
            new_launcher_create_time=new_ct,
            auth_token=server.auth_token,
        )
        with events_lock:
            events.append("reauth_done")
        reauth_result.append(res)

    try:
        t_unreg = threading.Thread(target=_run_unregister)
        t_reauth = threading.Thread(target=_run_reauth)
        t_unreg.start()
        t_reauth.start()

        assert in_unregister.wait(timeout=5.0)
        # Give reauthorize_launcher time to reach _identity_lock and block
        time.sleep(0.15)
        with events_lock:
            assert "reauth_done" not in events, (
                "reauthorize_launcher returned while UNREGISTER_HANDLE was still executing"
            )

        release_unregister.set()
        t_unreg.join(timeout=5.0)
        t_reauth.join(timeout=5.0)

        assert not t_unreg.is_alive()
        assert not t_reauth.is_alive()
        assert unreg_resp == {"ok": True, "removed": True}
        assert reauth_result == [(new_pid, new_ct)]
        assert events == [
            "unregister_start:exec-inflight",
            "unregister_end:exec-inflight",
            "reauth_done",
        ]
    finally:
        release_unregister.set()
        _terminate_proc(proc)


def test_unregister_suspended_after_initial_validation_rejected_after_reauthorize() -> None:
    """Simulates the exact race condition from the audit:
    Old Launcher passes step 3 initial identity validation, gets suspended before
    acquiring _identity_lock for UNREGISTER_HANDLE, reauthorize_launcher completes,
    and then UNREGISTER_HANDLE resumes and MUST be rejected without calling on_unregister."""
    old_pid, old_ct = _current_identity()
    proc, new_pid, new_ct = _spawn_live_process()

    unregistered: List[str] = []
    initial_check_done = threading.Event()
    allow_resume = threading.Event()

    server = JobHandleTransferServer(
        on_register=lambda *_args: (True, "ok"),
        on_unregister=lambda exec_id: (unregistered.append(exec_id) or True),
        auth_token="unreg-suspended-race-token",
        expected_launcher_pid=old_pid,
        expected_launcher_create_time=old_ct,
    )

    original_validate = server._validate_peer_identity

    def _pausing_validate(peer_identity: Optional[PeerProcessIdentity]) -> Optional[str]:
        res = original_validate(peer_identity)
        initial_check_done.set()
        assert allow_resume.wait(timeout=5.0)
        return res

    server._validate_peer_identity = _pausing_validate  # type: ignore[method-assign]

    unreg_resp: Dict[str, Any] = {}

    def _run_old_unregister() -> None:
        req = _signed_unregister_req(server, execution_id="exec-race-target", source_pid=old_pid)
        unreg_resp.update(
            server._handle_request(
                req,
                peer_identity=PeerProcessIdentity(pid=old_pid, process_create_time=old_ct),
            )
        )

    try:
        t_unreg = threading.Thread(target=_run_old_unregister)
        t_unreg.start()

        assert initial_check_done.wait(timeout=5.0)

        # Rotate launcher identity while old UNREGISTER_HANDLE is suspended
        res = server.reauthorize_launcher(
            new_launcher_pid=new_pid,
            new_launcher_create_time=new_ct,
            auth_token=server.auth_token,
        )
        assert res == (new_pid, new_ct)

        # Resume the suspended old UNREGISTER_HANDLE
        allow_resume.set()
        t_unreg.join(timeout=5.0)
        assert not t_unreg.is_alive()

        assert unreg_resp["ok"] is False
        assert unreg_resp["error"] == "unauthorized_launcher_pid"
        assert unregistered == [], "Revoked Launcher MUST NOT invoke on_unregister"
    finally:
        allow_resume.set()
        _terminate_proc(proc)


# ---------------------------------------------------------------------------
# 4. UNREGISTER com PID correto, mas create_time divergente: rejeitado
# ---------------------------------------------------------------------------


def test_unregister_matching_pid_divergent_create_time_rejected() -> None:
    old_pid, old_ct = _current_identity()
    unregistered: List[str] = []

    server = JobHandleTransferServer(
        on_register=lambda *_args: (True, "ok"),
        on_unregister=lambda exec_id: (unregistered.append(exec_id) or True),
        auth_token="unreg-ct-mismatch-token",
        expected_launcher_pid=old_pid,
        expected_launcher_create_time=old_ct,
    )

    req = _signed_unregister_req(server, execution_id="exec-pid-reused", source_pid=old_pid)
    resp = server._handle_request(
        req,
        peer_identity=PeerProcessIdentity(pid=old_pid, process_create_time=old_ct + 9999),
    )
    assert resp["ok"] is False
    assert resp["error"] == "launcher_pid_reused_or_mismatch"
    assert unregistered == []


# ---------------------------------------------------------------------------
# 5, 6, 7. UNREGISTER não autorizado não revoga handle de outra tentativa,
#          sem TerminateJobObject indevido e sem evidência falsa de terminação
# ---------------------------------------------------------------------------


def test_unauthorized_unregister_cannot_revoke_handle_no_spurious_terminate_or_evidence(
    tmp_path: Path,
) -> None:
    old_pid, old_ct = _current_identity()
    proc, new_pid, new_ct = _spawn_live_process()
    spy_api = _SpyTerminationApi()

    db_path = tmp_path / "watchdog_unreg.db"
    state_store = StateStore(db_path)
    outbox_store = OutboxStore(state_store)

    engine = SafeTerminationEngine(
        state_store=state_store,
        outbox_store=outbox_store,
        win32=spy_api,  # type: ignore[arg-type]
    )
    engine.register_job_handle(
        execution_id="exec-active-attempt-2",
        job_handle=101,
        owned=False,
        attempt_id="att-2",
        fencing_token=2,
        source_pid=new_pid,
        source_process_create_time=new_ct,
    )

    server = JobHandleTransferServer(
        on_register=lambda *_args: (True, "ok"),
        on_unregister=engine.unregister_job_handle,
        auth_token="unreg-protect-handle-token",
        expected_launcher_pid=old_pid,
        expected_launcher_create_time=old_ct,
    )

    try:
        # Reauthorize to new Launcher
        server.reauthorize_launcher(
            new_launcher_pid=new_pid,
            new_launcher_create_time=new_ct,
            auth_token=server.auth_token,
        )

        # Old Launcher attempts to revoke active attempt's handle
        req_old = _signed_unregister_req(
            server,
            execution_id="exec-active-attempt-2",
            source_pid=old_pid,
        )
        resp_old = server._handle_request(
            req_old,
            peer_identity=PeerProcessIdentity(pid=old_pid, process_create_time=old_ct),
        )
        assert resp_old["ok"] is False
        assert resp_old["error"] == "unauthorized_launcher_pid"

        # Handle of attempt-2 MUST still be registered and valid
        auth_handle = engine.get_authorized_job_handle("exec-active-attempt-2")
        assert auth_handle == 101

        # Zero spurious TerminateJobObject calls, zero closed handles, zero false termination evidence
        assert spy_api.terminate_job_calls == []
        assert spy_api.closed_handles == []
        assert outbox_store.get_pending_events() == []
    finally:
        engine.close()
        state_store.close()
        _terminate_proc(proc)


# ---------------------------------------------------------------------------
# 8. Nenhum deadlock durante shutdown concorrente com UNREGISTER e reauthorize
# ---------------------------------------------------------------------------


def test_no_deadlock_during_shutdown_with_concurrent_unregister_and_reauthorize(
    tmp_path: Path,
) -> None:
    old_pid, old_ct = _current_identity()
    proc, new_pid, new_ct = _spawn_live_process()
    spy_api = _SpyTerminationApi()

    db_path = tmp_path / "watchdog_shutdown.db"
    state_store = StateStore(db_path)

    engine = SafeTerminationEngine(
        state_store=state_store,
        win32=spy_api,  # type: ignore[arg-type]
    )
    engine.register_job_handle(
        execution_id="exec-shutdown-1",
        job_handle=202,
        owned=False,
        attempt_id="att-s1",
        fencing_token=1,
    )

    server = JobHandleTransferServer(
        on_register=lambda *_args: (True, "ok"),
        on_unregister=engine.unregister_job_handle,
        auth_token="unreg-shutdown-token",
        expected_launcher_pid=old_pid,
        expected_launcher_create_time=old_ct,
    )
    server.start()

    errors: List[Exception] = []

    def _spam_unregister() -> None:
        for i in range(20):
            try:
                req = _signed_unregister_req(
                    server,
                    execution_id=f"exec-shutdown-{i}",
                    source_pid=old_pid,
                )
                server._handle_request(
                    req,
                    peer_identity=PeerProcessIdentity(pid=old_pid, process_create_time=old_ct),
                )
            except Exception as exc:
                errors.append(exc)

    def _run_reauth() -> None:
        try:
            server.reauthorize_launcher(
                new_launcher_pid=new_pid,
                new_launcher_create_time=new_ct,
                auth_token=server.auth_token,
            )
        except Exception:
            # Expected if server.close() wins the race and sets _stop_event
            pass

    try:
        t1 = threading.Thread(target=_spam_unregister)
        t2 = threading.Thread(target=_run_reauth)
        t1.start()
        t2.start()

        # Close server while concurrent unregister & reauthorize are active
        closed = server.close(timeout_sec=3.0)
        assert closed is True

        t1.join(timeout=3.0)
        t2.join(timeout=3.0)
        assert not t1.is_alive(), "Deadlock detected in UNREGISTER_HANDLE thread"
        assert not t2.is_alive(), "Deadlock detected in reauthorize_launcher thread"
        assert errors == []
        assert spy_api.terminate_job_calls == []
    finally:
        engine.close()
        state_store.close()
        _terminate_proc(proc)
