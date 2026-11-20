"""Fail-closed Launcher reauthorization security tests (Sprint 2.7 - Task 6 Final Fix).

Validates that ``JobHandleTransferServer.reauthorize_launcher()`` and
``WatchdogDaemon.reauthorize_launcher()`` unconditionally require live Windows
kernel verification via ``GetProcessTimes``, cannot be bypassed in production,
preserve the previous authorized identity on every failure, and serialize
cleanly against concurrent ``REGISTER_HANDLE`` requests.
"""

from __future__ import annotations

import inspect
import os
import secrets
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Tuple

import pytest

from desktop_worker.watchdog.job_handle_transfer import (
    JobHandleTransferError,
    JobHandleTransferServer,
    PeerProcessIdentity,
    Win32HandleTransferApi,
    compute_ipc_signature,
)
from desktop_worker.watchdog.watchdog_daemon import WatchdogDaemon


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


def _make_server(
    *,
    expected_pid: int,
    expected_ct: int,
    auth_token: str = "fail-closed-reauth-token",
    on_register: Any = None,
    api: Any = None,
) -> JobHandleTransferServer:
    def _default_register(*_args: Any) -> Tuple[bool, str]:
        return True, "ok"

    return JobHandleTransferServer(
        on_register=on_register or _default_register,
        on_unregister=lambda _exec_id: True,
        auth_token=auth_token,
        expected_launcher_pid=expected_pid,
        expected_launcher_create_time=expected_ct,
        api=api,
    )


def _signed_register_req(
    server: JobHandleTransferServer,
    *,
    execution_id: str,
    job_handle: int,
    source_pid: int,
) -> Dict[str, Any]:
    req: Dict[str, Any] = {
        "op": "REGISTER_HANDLE",
        "execution_id": execution_id,
        "job_handle": job_handle,
        "attempt_id": "att-1",
        "fencing_token": 1,
        "source_pid": source_pid,
        "target_watchdog_pid": server.watchdog_pid,
        "target_watchdog_create_time": server.watchdog_create_time,
        "target_daemon_instance_id": server.daemon_instance_id,
        "nonce": secrets.token_hex(16),
        "timestamp": time.time(),
    }
    req["signature"] = compute_ipc_signature(server.auth_token, req)
    return req


# ---------------------------------------------------------------------------
# 1. Reautorização com processo ativo e identidade correta: aprovada
# ---------------------------------------------------------------------------


def test_reauthorize_live_process_valid_identity_approved() -> None:
    old_pid, old_ct = _current_identity()
    server = _make_server(expected_pid=old_pid, expected_ct=old_ct)

    proc, new_pid, new_ct = _spawn_live_process()
    try:
        res_pid, res_ct = server.reauthorize_launcher(
            new_launcher_pid=new_pid,
            new_launcher_create_time=new_ct,
            auth_token=server.auth_token,
            reason="live_process_rotation",
        )
        assert (res_pid, res_ct) == (new_pid, new_ct)
        assert server.expected_launcher_identity == (new_pid, new_ct)
    finally:
        _terminate_proc(proc)


# ---------------------------------------------------------------------------
# 2. PID inexistente: bloqueada + identidade anterior preservada
# ---------------------------------------------------------------------------


def test_reauthorize_nonexistent_pid_blocked_and_preserves_identity() -> None:
    old_pid, old_ct = _current_identity()
    server = _make_server(expected_pid=old_pid, expected_ct=old_ct)

    nonexistent_pid = 999_999_991
    with pytest.raises(JobHandleTransferError, match="Kernel verification failed"):
        server.reauthorize_launcher(
            new_launcher_pid=nonexistent_pid,
            new_launcher_create_time=123456789,
            auth_token=server.auth_token,
        )

    assert server.expected_launcher_identity == (old_pid, old_ct)


# ---------------------------------------------------------------------------
# 3. PID com create_time divergente: bloqueada + identidade anterior preservada
# ---------------------------------------------------------------------------


def test_reauthorize_mismatched_create_time_blocked_and_preserves_identity() -> None:
    old_pid, old_ct = _current_identity()
    server = _make_server(expected_pid=old_pid, expected_ct=old_ct)

    proc, new_pid, new_ct = _spawn_live_process()
    try:
        with pytest.raises(JobHandleTransferError, match="Kernel verification failed"):
            server.reauthorize_launcher(
                new_launcher_pid=new_pid,
                new_launcher_create_time=new_ct + 1,
                auth_token=server.auth_token,
            )

        assert server.expected_launcher_identity == (old_pid, old_ct)
    finally:
        _terminate_proc(proc)


# ---------------------------------------------------------------------------
# 4. Processo encerrado: bloqueado + identidade anterior preservada
# ---------------------------------------------------------------------------


def test_reauthorize_terminated_process_blocked_and_preserves_identity() -> None:
    old_pid, old_ct = _current_identity()
    server = _make_server(expected_pid=old_pid, expected_ct=old_ct)

    proc, dead_pid, dead_ct = _spawn_live_process()
    _terminate_proc(proc)
    assert proc.poll() is not None

    with pytest.raises(JobHandleTransferError, match="Kernel verification failed"):
        server.reauthorize_launcher(
            new_launcher_pid=dead_pid,
            new_launcher_create_time=dead_ct,
            auth_token=server.auth_token,
        )

    assert server.expected_launcher_identity == (old_pid, old_ct)


# ---------------------------------------------------------------------------
# 5. API Windows indisponível: bloqueada + identidade anterior preservada
# ---------------------------------------------------------------------------


def test_reauthorize_unavailable_windows_api_blocked_and_preserves_identity() -> None:
    old_pid, old_ct = _current_identity()
    server = _make_server(expected_pid=old_pid, expected_ct=old_ct)
    # Simulate unavailable Windows kernel API
    server._api = None

    proc, new_pid, new_ct = _spawn_live_process()
    try:
        with pytest.raises(JobHandleTransferError, match="Windows kernel API is unavailable"):
            server.reauthorize_launcher(
                new_launcher_pid=new_pid,
                new_launcher_create_time=new_ct,
                auth_token=server.auth_token,
            )

        assert server.expected_launcher_identity == (old_pid, old_ct)
    finally:
        _terminate_proc(proc)


# ---------------------------------------------------------------------------
# 6. Tentativa de desabilitar a verificação: impossível no caminho de produção
# ---------------------------------------------------------------------------


def test_reauthorize_cannot_disable_verification_in_production() -> None:
    server_sig = inspect.signature(JobHandleTransferServer.reauthorize_launcher)
    daemon_sig = inspect.signature(WatchdogDaemon.reauthorize_launcher)
    assert "verify_live_process" not in server_sig.parameters
    assert "verify_live_process" not in daemon_sig.parameters

    old_pid, old_ct = _current_identity()
    server = _make_server(expected_pid=old_pid, expected_ct=old_ct)

    bypass_kwargs: Dict[str, Any] = {"verify_live_process": False}
    with pytest.raises(TypeError, match="verify_live_process"):
        server.reauthorize_launcher(
            new_launcher_pid=999_999_991,
            new_launcher_create_time=123456789,
            auth_token=server.auth_token,
            **bypass_kwargs,
        )

    assert server.expected_launcher_identity == (old_pid, old_ct)


# ---------------------------------------------------------------------------
# 7. Falhas de validação (token/pid/ct): identidade anterior preservada
# ---------------------------------------------------------------------------


def test_reauthorize_validation_failures_preserve_previous_identity() -> None:
    old_pid, old_ct = _current_identity()
    server = _make_server(expected_pid=old_pid, expected_ct=old_ct)

    proc, new_pid, new_ct = _spawn_live_process()
    try:
        with pytest.raises(JobHandleTransferError, match="invalid auth_token"):
            server.reauthorize_launcher(
                new_launcher_pid=new_pid,
                new_launcher_create_time=new_ct,
                auth_token="wrong-token",
            )
        assert server.expected_launcher_identity == (old_pid, old_ct)

        with pytest.raises(ValueError, match="new_launcher_pid"):
            server.reauthorize_launcher(
                new_launcher_pid=0,
                new_launcher_create_time=new_ct,
                auth_token=server.auth_token,
            )
        assert server.expected_launcher_identity == (old_pid, old_ct)

        with pytest.raises(ValueError, match="new_launcher_create_time"):
            server.reauthorize_launcher(
                new_launcher_pid=new_pid,
                new_launcher_create_time=-5,
                auth_token=server.auth_token,
            )
        assert server.expected_launcher_identity == (old_pid, old_ct)
    finally:
        _terminate_proc(proc)


# ---------------------------------------------------------------------------
# 8. Reautorização concorrente com REGISTER_HANDLE: sem janela inconsistente
# ---------------------------------------------------------------------------


def test_reauthorize_concurrent_with_register_handle_no_inconsistent_window() -> None:
    old_pid, old_ct = _current_identity()
    proc, new_pid, new_ct = _spawn_live_process()

    events: List[Tuple[str, Any]] = []
    events_lock = threading.Lock()
    in_register = threading.Event()
    release_register = threading.Event()

    def _slow_on_register(
        execution_id: str,
        job_handle: int,
        attempt_id: str,
        fencing_token: int,
        source_pid: int,
        source_ct: int,
    ) -> Tuple[bool, str]:
        in_register.set()
        release_register.wait(timeout=5.0)
        with events_lock:
            events.append(("register_commit", source_pid, source_ct))
        return True, "registered"

    server = _make_server(
        expected_pid=old_pid,
        expected_ct=old_ct,
        on_register=_slow_on_register,
    )

    try:
        # Part A: REGISTER_HANDLE from old launcher is in-flight inside _on_register
        # when reauthorize_launcher() is called. reauthorize_launcher() MUST wait
        # for the in-flight registration to finish before completing, so no old-identity
        # registration can commit AFTER reauthorize_launcher() returns.
        req_old_inflight = _signed_register_req(
            server, execution_id="exec-inflight", job_handle=4001, source_pid=old_pid
        )
        old_peer = PeerProcessIdentity(pid=old_pid, process_create_time=old_ct)

        reg_result: Dict[str, Any] = {}

        def _run_inflight_register() -> None:
            reg_result["resp"] = server._handle_request(req_old_inflight, peer_identity=old_peer)

        t_reg = threading.Thread(target=_run_inflight_register)
        t_reg.start()
        assert in_register.wait(timeout=5.0)

        reauth_done = threading.Event()
        reauth_result: Dict[str, Any] = {}

        def _run_reauth() -> None:
            reauth_result["identity"] = server.reauthorize_launcher(
                new_launcher_pid=new_pid,
                new_launcher_create_time=new_ct,
                auth_token=server.auth_token,
            )
            with events_lock:
                events.append(("reauth_completed", new_pid, new_ct))
            reauth_done.set()

        t_reauth = threading.Thread(target=_run_reauth)
        t_reauth.start()

        # Reauthorization must NOT complete while old REGISTER_HANDLE is still in critical section
        assert not reauth_done.wait(timeout=0.15)

        release_register.set()
        t_reg.join(timeout=5.0)
        t_reauth.join(timeout=5.0)

        assert reg_result["resp"]["ok"] is True
        assert reauth_result["identity"] == (new_pid, new_ct)
        assert events == [
            ("register_commit", old_pid, old_ct),
            ("reauth_completed", new_pid, new_ct),
        ]

        # Part B: Immediately after reauthorization completes, any REGISTER_HANDLE
        # from the old launcher is rejected and never reaches _on_register.
        req_old_after = _signed_register_req(
            server, execution_id="exec-after", job_handle=4002, source_pid=old_pid
        )
        resp_after = server._handle_request(req_old_after, peer_identity=old_peer)
        assert resp_after["ok"] is False
        assert resp_after["error"] == "unauthorized_launcher_pid"
        assert len(events) == 2

        # New launcher REGISTER_HANDLE is accepted
        req_new = _signed_register_req(
            server, execution_id="exec-new", job_handle=4003, source_pid=new_pid
        )
        new_peer = PeerProcessIdentity(pid=new_pid, process_create_time=new_ct)
        resp_new = server._handle_request(req_new, peer_identity=new_peer)
        assert resp_new["ok"] is True
        assert events[-1] == ("register_commit", new_pid, new_ct)
    finally:
        release_register.set()
        _terminate_proc(proc)


# ---------------------------------------------------------------------------
# 9. Docstring confirma pinning obrigatório (não opcional)
# ---------------------------------------------------------------------------


def test_server_docstring_states_mandatory_launcher_pinning() -> None:
    doc = JobHandleTransferServer.__doc__ or ""
    assert "Mandatory" in doc
    assert "Optional pinning" not in doc
