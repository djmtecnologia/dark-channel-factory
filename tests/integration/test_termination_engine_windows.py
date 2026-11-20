"""Real Windows 11 integration tests for Safe Process Termination Engine (Sprint 2.7 - Task 6).

Executes real harmless Python processes isolated inside real Win32 Job Objects
and validates identity verification, process-tree termination, accounting/PID
verification, PID-reuse blocking, already-terminated handling, timeout handling,
and real SQLite State Store + Outbox persistence.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only integration tests", allow_module_level=True)

from desktop_worker.watchdog.identity import (
    CanonicalExecutionIdentity,
    IdentityStatus,
    IdentityValidator,
)
from desktop_worker.watchdog.job_object import (
    JobObjectProcessManager,
    Win32Api,
)
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


def _seed_execution(
    tmp_path: Path,
    *,
    execution_id: str,
    pid: int,
    create_time: int,
    job_object_name: str,
    fencing_token: int = 1,
) -> tuple[StateStore, OutboxStore, CanonicalExecutionIdentity]:
    store = StateStore(tmp_path / f"{execution_id}.db")
    outbox = OutboxStore(store)
    ident = CanonicalExecutionIdentity(
        execution_id=execution_id,
        attempt_id=f"att-{execution_id}",
        job_id=f"job-{execution_id}",
        tenant_id="tenant-win",
        channel_id="chan-win",
        worker_id="worker-win",
        fencing_token=fencing_token,
        pid=pid,
        process_create_time=create_time,
        job_object_name=job_object_name,
        browser_user_data_dir=str(tmp_path / "profile"),
    )
    store.upsert_attempt(
        attempt_id=ident.attempt_id,
        job_id=ident.job_id,
        fencing_token=ident.fencing_token,
        hub_status=AttemptHubStatus.IN_PROGRESS,
        last_heartbeat_at_utc="2026-11-19T10:00:00+00:00",
    )
    store.record_execution(
        ident,
        boot_time_utc="2026-11-19T10:00:00+00:00",
        status=ExecutionStatus.RUNNING,
    )
    return store, outbox, ident


def test_windows_single_isolated_process_termination_and_persistence(tmp_path: Path) -> None:
    """Scenarios 1, 4, 5, 10: isolated process terminated, ActiveProcesses=0, PIDs=[], SQLite persisted."""
    mgr = JobObjectProcessManager()
    cmd = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    launch = mgr.launch(cmd)
    assert launch.ok and launch.process is not None
    proc = launch.process
    store = None
    try:
        assert proc.identity.process_creation_time is not None
        assert proc.identity.job_handle is not None
        store, outbox, ident = _seed_execution(
            tmp_path,
            execution_id="win-single",
            pid=proc.pid,
            create_time=proc.identity.process_creation_time,
            job_object_name="dcf-win-single",
        )
        engine = SafeTerminationEngine(store, outbox_store=outbox)
        engine.register_job_handle(ident.execution_id, proc.identity.job_handle)
        outcome = engine.terminate_execution(
            TerminationContext(
                identity=ident,
                job_handle=proc.identity.job_handle,
                process_handle=proc.identity.process_handle,
                event_id="evt-win-single",
            ),
            timeout_ms=5000,
        )

        assert outcome.verification_result == TerminationVerificationResult.TREE_TERMINATED
        assert outcome.fully_completed is True
        assert outcome.active_processes_before == 1
        assert outcome.active_processes_after == 0
        assert outcome.pids_after == []
        assert outcome.pids_truncated is False
        assert outcome.evidence_persisted is True
        assert outcome.outbox_enqueued is True

        ev = store.get_evidence("evt-win-single")
        assert ev is not None
        assert ev.verification_result == "TREE_TERMINATED"
        payload = json.loads(ev.payload_json)
        assert payload["active_processes_after"] == 0
        assert payload["pids_after"] == []
        assert payload["pids_truncated"] is False

        outbox_ev = outbox.get_event("evt-win-single")
        assert outbox_ev is not None
        assert outbox_ev.event_type == TerminationOutboxEventType.TERMINATION_CONFIRMED.value
        assert outbox_ev.status == OutboxEventStatus.PENDING
        assert all(r.is_valid for r in store.verify_integrity())
    finally:
        proc.close()
        if store is not None:
            store.close()


def test_windows_parent_and_child_tree_termination(tmp_path: Path) -> None:
    """Scenarios 2, 3, 4, 5: parent + child process tree terminated cleanly."""
    mgr = JobObjectProcessManager()
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess, sys, time; "
        f"p = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(60)"
    )
    cmd = f'"{sys.executable}" -c "{parent_code}"'
    launch = mgr.launch(cmd)
    assert launch.ok and launch.process is not None
    proc = launch.process
    store = None
    try:
        # Wait briefly until child process is spawned inside the Job Object
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            acct, _ = proc.query_basic_accounting()
            if acct is not None and acct.active_processes >= 2:
                break
            time.sleep(0.05)

        acct_before, _ = proc.query_basic_accounting()
        assert acct_before is not None and acct_before.active_processes >= 2

        store, outbox, ident = _seed_execution(
            tmp_path,
            execution_id="win-tree",
            pid=proc.pid,
            create_time=proc.identity.process_creation_time or 0,
            job_object_name="dcf-win-tree",
        )
        engine = SafeTerminationEngine(store, outbox_store=outbox)
        engine.register_job_handle(ident.execution_id, proc.identity.job_handle)
        outcome = engine.terminate_execution(
            TerminationContext(
                identity=ident,
                job_handle=proc.identity.job_handle,
                process_handle=proc.identity.process_handle,
                event_id="evt-win-tree",
            ),
            timeout_ms=5000,
        )

        assert outcome.verification_result == TerminationVerificationResult.TREE_TERMINATED
        assert outcome.fully_completed is True
        assert outcome.active_processes_before is not None and outcome.active_processes_before >= 2
        assert outcome.active_processes_after == 0
        assert outcome.pids_after == []
        assert outcome.pids_truncated is False
    finally:
        proc.close()
        if store is not None:
            store.close()


def test_windows_already_terminated_process(tmp_path: Path) -> None:
    """Scenario 6: process already exited before termination request."""
    mgr = JobObjectProcessManager()
    cmd = f'"{sys.executable}" -c "pass"'
    launch = mgr.launch(cmd)
    assert launch.ok and launch.process is not None
    proc = launch.process
    store = None
    try:
        # Wait for the short-lived process to exit naturally
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            acct, _ = proc.query_basic_accounting()
            if acct is not None and acct.active_processes == 0:
                break
            time.sleep(0.05)

        store, outbox, ident = _seed_execution(
            tmp_path,
            execution_id="win-already-exited",
            pid=proc.pid,
            create_time=proc.identity.process_creation_time or 0,
            job_object_name="dcf-win-exited",
        )
        # Close process_handle so OS can fully reap PID if needed; keep job_handle open
        win32 = Win32Api()
        if proc.identity.process_handle:
            win32.close_handle(proc.identity.process_handle)
            proc.identity.process_handle = None

        engine = SafeTerminationEngine(store, outbox_store=outbox)
        engine.register_job_handle(ident.execution_id, proc.identity.job_handle)
        outcome = engine.terminate_execution(
            TerminationContext(
                identity=ident,
                job_handle=proc.identity.job_handle,
                event_id="evt-win-exited",
            ),
            timeout_ms=5000,
        )

        assert outcome.verification_result == TerminationVerificationResult.TREE_TERMINATED
        assert outcome.fully_completed is True
        assert outcome.active_processes_after == 0
        assert outcome.pids_after == []
    finally:
        proc.close()
        if store is not None:
            store.close()


def test_windows_divergent_identity_blocks_termination(tmp_path: Path) -> None:
    """Scenario 7: wrong process_create_time triggers PID_REUSED and blocks termination."""
    mgr = JobObjectProcessManager()
    cmd = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    launch = mgr.launch(cmd)
    assert launch.ok and launch.process is not None
    proc = launch.process
    store = None
    try:
        wrong_create_time = (proc.identity.process_creation_time or 1000) + 999999
        store, outbox, ident = _seed_execution(
            tmp_path,
            execution_id="win-divergent",
            pid=proc.pid,
            create_time=wrong_create_time,
            job_object_name="dcf-win-divergent",
        )
        engine = SafeTerminationEngine(store, outbox_store=outbox)
        engine.register_job_handle(ident.execution_id, proc.identity.job_handle)
        outcome = engine.terminate_execution(
            TerminationContext(
                identity=ident,
                job_handle=proc.identity.job_handle,
                event_id="evt-win-divergent",
            )
        )

        assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
        assert outcome.classification == OrphanClassification.IDENTITY_MISMATCH
        assert outcome.identity_status == IdentityStatus.PID_REUSED
        assert outcome.fully_completed is False
        assert outcome.evidence_persisted is True

        # Process must still be alive because termination was blocked!
        acct, _ = proc.query_basic_accounting()
        assert acct is not None and acct.active_processes == 1
    finally:
        proc.terminate_job(timeout_ms=3000)
        proc.close()
        if store is not None:
            store.close()



def test_windows_watchdog_daemon_ipc_auth_spoof_replay_and_revocation(tmp_path: Path) -> None:
    """Real Windows 11 independent-process IPC security verification:
    1. Independent WatchdogDaemon starts its integrated JobHandleTransferServer.
    2. Unauthenticated client is rejected.
    3. Client spoofing source_pid is rejected via kernel GetExtendedTcpTable.
    4. Legitimate Launcher duplicates handle & registers; replay of same request is rejected.
    5. Revocation (UNREGISTER_HANDLE) blocks termination and closes owned handle.
    6. Re-registration allows termination of only the authorized tree.
    7. WatchdogDaemon shutdown stops the IPC server cleanly.
    """
    import os
    import socket

    from desktop_worker.watchdog.job_handle_transfer import (
        JobHandleTransferClient,
        duplicate_job_handle_to_process,
    )

    mgr = JobObjectProcessManager()
    launch = mgr.launch(f'"{sys.executable}" -c "import time; time.sleep(60)"')
    assert launch.ok and launch.process is not None
    proc = launch.process
    store = None
    wd_proc = None
    try:
        store, _outbox, ident = _seed_execution(
            tmp_path,
            execution_id="win-ipc-sec",
            pid=proc.pid,
            create_time=proc.identity.process_creation_time or 0,
            job_object_name="dcf-win-ipc-sec",
        )
        db_path = tmp_path / "win-ipc-sec.db"
        auth_token = "win11-real-hmac-secret-token"
        wd_proc, info = _spawn_watchdog_subprocess(tmp_path, db_path, auth_token, "ipcsec")
        assert info["ipc_running"] is True
        transfer_port = info["transfer_port"]

        # 1. Unauthenticated client rejected
        bad_client = JobHandleTransferClient(port=transfer_port, auth_token="invalid-token")
        resp_unauth = bad_client._send({"op": "PING"})
        assert resp_unauth["ok"] is False
        assert resp_unauth["error"] == "unauthorized"

        # 2. Spoofed source_pid rejected by real Windows kernel GetExtendedTcpTable
        client = JobHandleTransferClient(port=transfer_port, auth_token=auth_token)
        resp_spoof = client._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 1234,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": os.getpid() + 7777,
            }
        )
        assert resp_spoof["ok"] is False
        assert resp_spoof["error"] == "spoofed_source_pid"

        # 3. Legitimate DuplicateHandle + signed REGISTER_HANDLE + replay rejection
        ping = client.ping_watchdog()
        assert ping.watchdog_pid == info["pid"]
        assert ping.daemon_instance_id == info["daemon_instance_id"]
        dup_h = duplicate_job_handle_to_process(
            proc.identity.job_handle,
            ping.watchdog_pid,
            expected_target_create_time=ping.watchdog_create_time,
        )
        signed_reg = client._build_signed_request(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": dup_h,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": os.getpid(),
                "target_watchdog_pid": ping.watchdog_pid,
                "target_watchdog_create_time": ping.watchdog_create_time,
                "target_daemon_instance_id": ping.daemon_instance_id,
            }
        )
        resp_ok = client._send_raw(signed_reg)
        assert resp_ok["ok"] is True

        # Replay of exact same signed REGISTER_HANDLE -> REJECTED
        resp_replay = client._send_raw(signed_reg)
        assert resp_replay["ok"] is False
        assert resp_replay["error"] == "replay_detected"

        # 4. Revocation blocks termination
        assert client.unregister(ident.execution_id) is True
        ident_dict = {
            "execution_id": ident.execution_id,
            "attempt_id": ident.attempt_id,
            "job_id": ident.job_id,
            "tenant_id": ident.tenant_id,
            "channel_id": ident.channel_id,
            "worker_id": ident.worker_id,
            "fencing_token": ident.fencing_token,
            "pid": ident.pid,
            "process_create_time": ident.process_create_time,
            "job_object_name": ident.job_object_name,
            "browser_user_data_dir": ident.browser_user_data_dir,
        }
        blocked = _send_ctrl(
            info["ctrl_port"],
            {"op": "TERMINATE", "identity": ident_dict, "job_handle": dup_h, "event_id": "evt-revoked"},
        )
        assert blocked["error_code"] == "JOB_HANDLE_NOT_AUTHORIZED"
        assert blocked["verification_result"] == "INCONCLUSIVE"
        assert blocked["evidence_persisted"] is False

        # 5. Re-transfer & terminate cleanly
        wd_pid2, dup_h2 = client.transfer_and_register(
            execution_id=ident.execution_id,
            source_job_handle=proc.identity.job_handle,
            attempt_id=ident.attempt_id,
            fencing_token=ident.fencing_token,
        )
        assert wd_pid2 == info["pid"]
        term_ok = _send_ctrl(
            info["ctrl_port"],
            {"op": "TERMINATE", "identity": ident_dict, "job_handle": dup_h2, "event_id": "evt-ipcsec-ok"},
        )
        assert term_ok["verification_result"] == "TREE_TERMINATED"
        assert term_ok["fully_completed"] is True
        assert term_ok["active_processes_after"] == 0

        # 6. WatchdogDaemon shuts down and closes IPC server
        _send_ctrl(info["ctrl_port"], {"op": "SHUTDOWN"})
        wd_proc.wait(timeout=5)
        with pytest.raises(OSError):
            with socket.create_connection(("127.0.0.1", transfer_port), timeout=0.5):
                pass
    finally:
        if wd_proc is not None and wd_proc.poll() is None:
            wd_proc.kill()
        proc.terminate_job(timeout_ms=3000)
        proc.close()
        if store is not None:
            store.close()


def test_windows_nonexistent_pid(tmp_path: Path) -> None:
    """Scenario 8: non-existent PID with empty Job Object confirms tree empty."""
    mgr = JobObjectProcessManager()
    cmd = f'"{sys.executable}" -c "pass"'
    launch = mgr.launch(cmd)
    assert launch.ok and launch.process is not None
    proc = launch.process
    store = None
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            acct, _ = proc.query_basic_accounting()
            if acct is not None and acct.active_processes == 0:
                break
            time.sleep(0.05)

        fake_pid = 99999999
        store, outbox, ident = _seed_execution(
            tmp_path,
            execution_id="win-nonexistent-pid",
            pid=fake_pid,
            create_time=133000000000000000,
            job_object_name="dcf-win-nonexistent",
        )
        engine = SafeTerminationEngine(store, outbox_store=outbox)
        engine.register_job_handle(ident.execution_id, proc.identity.job_handle)
        outcome = engine.terminate_execution(
            TerminationContext(
                identity=ident,
                job_handle=proc.identity.job_handle,
                event_id="evt-win-nonexistent",
            )
        )

        assert outcome.identity_status == IdentityStatus.PROCESS_NOT_FOUND
        assert outcome.classification == OrphanClassification.ALREADY_TERMINATED
        assert outcome.verification_result == TerminationVerificationResult.TREE_TERMINATED
        assert outcome.evidence_persisted is True
    finally:
        proc.close()
        if store is not None:
            store.close()


def test_windows_termination_timeout_handling(tmp_path: Path) -> None:
    """Scenario 9: timeout during verification is classified TERMINATION_TIMEOUT, not TREE_TERMINATED."""
    mgr = JobObjectProcessManager()
    cmd = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    launch = mgr.launch(cmd)
    assert launch.ok and launch.process is not None
    proc = launch.process
    store = None
    try:
        store, outbox, ident = _seed_execution(
            tmp_path,
            execution_id="win-timeout",
            pid=proc.pid,
            create_time=proc.identity.process_creation_time or 0,
            job_object_name="dcf-win-timeout",
        )
        real_win32 = Win32Api()

        class _NoOpTerminateWin32(Win32Api):
            """Uses real Win32 queries/validation, but simulates slow/unresponsive termination."""

            def terminate_job_object(self, job_handle: int, exit_code: int = 1) -> bool:
                return True

        wrapped_win32 = _NoOpTerminateWin32()
        engine = SafeTerminationEngine(
            store,
            outbox_store=outbox,
            win32=wrapped_win32,
            identity_validator=IdentityValidator(win32=real_win32),
            poll_interval_ms=20,
        )
        engine.register_job_handle(ident.execution_id, proc.identity.job_handle)
        outcome = engine.terminate_execution(
            TerminationContext(
                identity=ident,
                job_handle=proc.identity.job_handle,
                process_handle=proc.identity.process_handle,
                event_id="evt-win-timeout",
            ),
            timeout_ms=80,
        )

        assert outcome.verification_result == TerminationVerificationResult.TERMINATION_TIMEOUT
        assert outcome.fully_completed is False
        assert outcome.evidence_persisted is True
        assert outcome.active_processes_after == 1
    finally:
        proc.terminate_job(timeout_ms=3000)
        proc.close()
        if store is not None:
            store.close()



def test_windows_watchdog_daemon_ipc_auth_spoof_replay_and_revocation(tmp_path: Path) -> None:
    """Real Windows 11 independent-process IPC security verification:
    1. Independent WatchdogDaemon starts its integrated JobHandleTransferServer.
    2. Unauthenticated client is rejected.
    3. Client spoofing source_pid is rejected via kernel GetExtendedTcpTable.
    4. Legitimate Launcher duplicates handle & registers; replay of same request is rejected.
    5. Revocation (UNREGISTER_HANDLE) blocks termination and closes owned handle.
    6. Re-registration allows termination of only the authorized tree.
    7. WatchdogDaemon shutdown stops the IPC server cleanly.
    """
    import os
    import socket

    from desktop_worker.watchdog.job_handle_transfer import (
        JobHandleTransferClient,
        duplicate_job_handle_to_process,
    )

    mgr = JobObjectProcessManager()
    launch = mgr.launch(f'"{sys.executable}" -c "import time; time.sleep(60)"')
    assert launch.ok and launch.process is not None
    proc = launch.process
    store = None
    wd_proc = None
    try:
        store, _outbox, ident = _seed_execution(
            tmp_path,
            execution_id="win-ipc-sec",
            pid=proc.pid,
            create_time=proc.identity.process_creation_time or 0,
            job_object_name="dcf-win-ipc-sec",
        )
        db_path = tmp_path / "win-ipc-sec.db"
        auth_token = "win11-real-hmac-secret-token"
        wd_proc, info = _spawn_watchdog_subprocess(tmp_path, db_path, auth_token, "ipcsec")
        assert info["ipc_running"] is True
        transfer_port = info["transfer_port"]

        # 1. Unauthenticated client rejected
        bad_client = JobHandleTransferClient(port=transfer_port, auth_token="invalid-token")
        resp_unauth = bad_client._send({"op": "PING"})
        assert resp_unauth["ok"] is False
        assert resp_unauth["error"] == "unauthorized"

        # 2. Spoofed source_pid rejected by real Windows kernel GetExtendedTcpTable
        client = JobHandleTransferClient(port=transfer_port, auth_token=auth_token)
        resp_spoof = client._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 1234,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": os.getpid() + 7777,
            }
        )
        assert resp_spoof["ok"] is False
        assert resp_spoof["error"] == "spoofed_source_pid"

        # 3. Legitimate DuplicateHandle + signed REGISTER_HANDLE + replay rejection
        ping = client.ping_watchdog()
        assert ping.watchdog_pid == info["pid"]
        assert ping.daemon_instance_id == info["daemon_instance_id"]
        dup_h = duplicate_job_handle_to_process(
            proc.identity.job_handle,
            ping.watchdog_pid,
            expected_target_create_time=ping.watchdog_create_time,
        )
        signed_reg = client._build_signed_request(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": dup_h,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": os.getpid(),
                "target_watchdog_pid": ping.watchdog_pid,
                "target_watchdog_create_time": ping.watchdog_create_time,
                "target_daemon_instance_id": ping.daemon_instance_id,
            }
        )
        resp_ok = client._send_raw(signed_reg)
        assert resp_ok["ok"] is True

        # Replay of exact same signed REGISTER_HANDLE -> REJECTED
        resp_replay = client._send_raw(signed_reg)
        assert resp_replay["ok"] is False
        assert resp_replay["error"] == "replay_detected"

        # 4. Revocation blocks termination
        assert client.unregister(ident.execution_id) is True
        ident_dict = {
            "execution_id": ident.execution_id,
            "attempt_id": ident.attempt_id,
            "job_id": ident.job_id,
            "tenant_id": ident.tenant_id,
            "channel_id": ident.channel_id,
            "worker_id": ident.worker_id,
            "fencing_token": ident.fencing_token,
            "pid": ident.pid,
            "process_create_time": ident.process_create_time,
            "job_object_name": ident.job_object_name,
            "browser_user_data_dir": ident.browser_user_data_dir,
        }
        blocked = _send_ctrl(
            info["ctrl_port"],
            {"op": "TERMINATE", "identity": ident_dict, "job_handle": dup_h, "event_id": "evt-revoked"},
        )
        assert blocked["error_code"] == "JOB_HANDLE_NOT_AUTHORIZED"
        assert blocked["verification_result"] == "INCONCLUSIVE"
        assert blocked["evidence_persisted"] is False

        # 5. Re-transfer & terminate cleanly
        wd_pid2, dup_h2 = client.transfer_and_register(
            execution_id=ident.execution_id,
            source_job_handle=proc.identity.job_handle,
            attempt_id=ident.attempt_id,
            fencing_token=ident.fencing_token,
        )
        assert wd_pid2 == info["pid"]
        term_ok = _send_ctrl(
            info["ctrl_port"],
            {"op": "TERMINATE", "identity": ident_dict, "job_handle": dup_h2, "event_id": "evt-ipcsec-ok"},
        )
        assert term_ok["verification_result"] == "TREE_TERMINATED"
        assert term_ok["fully_completed"] is True
        assert term_ok["active_processes_after"] == 0

        # 6. WatchdogDaemon shuts down and closes IPC server
        _send_ctrl(info["ctrl_port"], {"op": "SHUTDOWN"})
        wd_proc.wait(timeout=5)
        with pytest.raises(OSError):
            with socket.create_connection(("127.0.0.1", transfer_port), timeout=0.5):
                pass
    finally:
        if wd_proc is not None and wd_proc.poll() is None:
            wd_proc.kill()
        proc.terminate_job(timeout_ms=3000)
        proc.close()
        if store is not None:
            store.close()


def test_windows_other_job_object_handle_blocks_termination(tmp_path: Path) -> None:
    """Negative test: passing a real handle of another Job Object must NOT call TerminateJobObject."""
    mgr = JobObjectProcessManager()
    cmd = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    launch_a = mgr.launch(cmd)
    launch_b = mgr.launch(cmd)
    assert launch_a.ok and launch_a.process is not None
    assert launch_b.ok and launch_b.process is not None
    proc_a = launch_a.process
    proc_b = launch_b.process
    store = None
    try:
        assert proc_a.identity.job_handle is not None
        assert proc_b.identity.job_handle is not None
        store, outbox, ident_a = _seed_execution(
            tmp_path,
            execution_id="win-other-job-a",
            pid=proc_a.pid,
            create_time=proc_a.identity.process_creation_time or 0,
            job_object_name="dcf-win-job-a",
        )

        class _SpyWin32(Win32Api):
            def __init__(self) -> None:
                super().__init__()
                self.terminate_calls: list[tuple[int, int]] = []

            def terminate_job_object(self, job_handle: int, exit_code: int = 1) -> bool:
                self.terminate_calls.append((job_handle, exit_code))
                return super().terminate_job_object(job_handle, exit_code)

        spy = _SpyWin32()
        engine = SafeTerminationEngine(store, outbox_store=outbox, win32=spy)
        engine.register_job_handle(ident_a.execution_id, proc_a.identity.job_handle)

        # Pass Job Object B's real handle instead of Job Object A's authorized handle
        outcome = engine.terminate_execution(
            TerminationContext(
                identity=ident_a,
                job_handle=proc_b.identity.job_handle,
                process_handle=proc_a.identity.process_handle,
            )
        )

        assert spy.terminate_calls == []
        assert outcome.verification_result == TerminationVerificationResult.INCONCLUSIVE
        assert outcome.classification == OrphanClassification.IDENTITY_MISMATCH
        assert outcome.error_code == "JOB_HANDLE_MISMATCH"
        assert outcome.fully_completed is False

        # Both isolated processes must still be alive
        acct_a, _ = proc_a.query_basic_accounting()
        acct_b, _ = proc_b.query_basic_accounting()
        assert acct_a is not None and acct_a.active_processes == 1
        assert acct_b is not None and acct_b.active_processes == 1
    finally:
        proc_a.terminate_job(timeout_ms=3000)
        proc_b.terminate_job(timeout_ms=3000)
        proc_a.close()
        proc_b.close()
        if store is not None:
            store.close()


def test_windows_real_idempotent_termination_across_reopen(tmp_path: Path) -> None:
    """Real Windows 11 test: repeating termination after StateStore reopen preserves original evidence & timestamps."""
    mgr = JobObjectProcessManager()
    cmd = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    launch = mgr.launch(cmd)
    assert launch.ok and launch.process is not None
    proc = launch.process
    store = None
    store2 = None
    try:
        assert proc.identity.job_handle is not None
        store, outbox, ident = _seed_execution(
            tmp_path,
            execution_id="win-idempotent",
            pid=proc.pid,
            create_time=proc.identity.process_creation_time or 0,
            job_object_name="dcf-win-idempotent",
        )
        engine1 = SafeTerminationEngine(store, outbox_store=outbox)
        engine1.register_job_handle(ident.execution_id, proc.identity.job_handle)

        first = engine1.terminate_execution(
            TerminationContext(
                identity=ident,
                job_handle=proc.identity.job_handle,
                process_handle=proc.identity.process_handle,
            ),
            timeout_ms=5000,
        )
        assert first.verification_result == TerminationVerificationResult.TREE_TERMINATED
        assert first.fully_completed is True
        assert first.evidence_record is not None
        orig_event_id = first.evidence_record.event_id
        orig_req_ts = first.evidence_record.termination_requested_at_utc
        orig_ver_ts = first.evidence_record.verified_at_utc
        store.close()
        store = None

        # Reopen StateStore/OutboxStore (simulated process restart) and close process_handle
        time.sleep(0.02)
        win32 = Win32Api()
        if proc.identity.process_handle:
            win32.close_handle(proc.identity.process_handle)
            proc.identity.process_handle = None

        store2 = StateStore(tmp_path / "win-idempotent.db")
        outbox2 = OutboxStore(store2)
        engine2 = SafeTerminationEngine(store2, outbox_store=outbox2)
        engine2.register_job_handle(ident.execution_id, proc.identity.job_handle)

        second = engine2.terminate_execution(
            TerminationContext(
                identity=ident,
                job_handle=proc.identity.job_handle,
            ),
            timeout_ms=5000,
        )
        assert second.verification_result == TerminationVerificationResult.TREE_TERMINATED
        assert second.fully_completed is True
        assert second.evidence_record is not None
        assert second.evidence_record.event_id == orig_event_id
        assert second.evidence_record.termination_requested_at_utc == orig_req_ts
        assert second.evidence_record.verified_at_utc == orig_ver_ts
        assert len(outbox2.get_pending_events()) == 1
        assert all(r.is_valid for r in store2.verify_integrity())
    finally:
        proc.close()
        if store is not None:
            store.close()
        if store2 is not None:
            store2.close()


# ---------------------------------------------------------------------------
# Cross-process Launcher <-> Independent Watchdog Handle Transfer Tests
# ---------------------------------------------------------------------------

_WATCHDOG_SUBPROCESS_SCRIPT = r'''
import json
import os
import socket
import sys
from pathlib import Path

from desktop_worker.watchdog.identity import CanonicalExecutionIdentity
from desktop_worker.watchdog.job_handle_transfer import (
    Win32HandleTransferApi,
)
from desktop_worker.watchdog.outbox import OutboxStore
from desktop_worker.watchdog.state_store import StateStore
from desktop_worker.watchdog.termination_engine import (
    SafeTerminationEngine,
    TerminationContext,
)
from desktop_worker.watchdog.watchdog_daemon import (
    WatchdogConfig,
    WatchdogDaemon,
)

db_path = Path(sys.argv[1])
auth_token = sys.argv[2]
ready_file = Path(sys.argv[3])
worker_id = sys.argv[4]
expected_launcher_pid = int(sys.argv[5])
expected_launcher_create_time = int(sys.argv[6])

store = StateStore(db_path)
outbox = OutboxStore(store)
engine = SafeTerminationEngine(store, outbox_store=outbox)
api = Win32HandleTransferApi()

cfg = WatchdogConfig(
    worker_id=worker_id,
    db_path=db_path,
    poll_interval_sec=1.0,
    heartbeat_interval_sec=5.0,
    heartbeat_timeout_sec=30.0,
)
daemon = WatchdogDaemon(
    cfg,
    termination_engine=engine,
    ipc_auth_token=auth_token,
    expected_launcher_pid=expected_launcher_pid,
    expected_launcher_create_time=expected_launcher_create_time,
)
daemon.start()
transfer_port = daemon.ipc_port

# Control socket for test harness to invoke request_safe_termination inside the independent WatchdogDaemon
ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
ctrl_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
ctrl_sock.bind(("127.0.0.1", 0))
ctrl_sock.listen(4)
ctrl_port = int(ctrl_sock.getsockname()[1])

ready_file.write_text(
    json.dumps({
        "pid": os.getpid(),
        "transfer_port": transfer_port,
        "ctrl_port": ctrl_port,
        "daemon_instance_id": daemon.daemon_instance_id,
        "ipc_running": daemon.ipc_running,
    }),
    encoding="utf-8",
)

try:
    while True:
        conn, _ = ctrl_sock.accept()
        with conn:
            raw = b""
            while not raw.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
            if not raw:
                continue
            cmd = json.loads(raw.decode("utf-8"))
            op = cmd.get("op")
            if op == "SHUTDOWN":
                conn.sendall((json.dumps({"ok": True}) + "\n").encode("utf-8"))
                break
            elif op == "HANDLE_COUNT":
                ok_c, cnt, _ = api.get_process_handle_count()
                conn.sendall((json.dumps({"ok": ok_c, "handle_count": cnt}) + "\n").encode("utf-8"))
            elif op == "COMPARE":
                same = api.compare_object_handles(int(cmd["h1"]), int(cmd["h2"]))
                conn.sendall((json.dumps({"ok": True, "same": same}) + "\n").encode("utf-8"))
            elif op == "TERMINATE":
                ident = CanonicalExecutionIdentity(**cmd["identity"])
                ctx = TerminationContext(
                    identity=ident,
                    job_handle=cmd.get("job_handle"),
                    event_id=cmd.get("event_id"),
                )
                out = daemon.request_safe_termination(ctx, timeout_ms=5000)
                conn.sendall(
                    (
                        json.dumps(
                            {
                                "verification_result": out.verification_result.value,
                                "classification": out.classification.value,
                                "error_code": out.error_code,
                                "fully_completed": out.fully_completed,
                                "evidence_persisted": out.evidence_persisted,
                                "outbox_enqueued": out.outbox_enqueued,
                                "active_processes_after": out.active_processes_after,
                                "pids_after": out.pids_after,
                            }
                        )
                        + "\n"
                    ).encode("utf-8")
                )
finally:
    ctrl_sock.close()
    daemon.stop()
    store.close()
'''


def _send_ctrl(port: int, payload: dict) -> dict:
    import socket

    with socket.create_connection(("127.0.0.1", port), timeout=10.0) as sock:
        sock.settimeout(10.0)
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        raw = b""
        while not raw.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
    return json.loads(raw.decode("utf-8"))


def _spawn_watchdog_subprocess(
    tmp_path: Path,
    db_path: Path,
    auth_token: str,
    name: str,
    *,
    expected_launcher_pid: Optional[int] = None,
    expected_launcher_create_time: Optional[int] = None,
):
    import os
    import subprocess
    import uuid
    from desktop_worker.watchdog.job_handle_transfer import Win32HandleTransferApi

    repo_root = str(Path(__file__).resolve().parents[2])
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        f"{repo_root}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else repo_root
    )

    if expected_launcher_pid is None:
        expected_launcher_pid = os.getpid()
    if expected_launcher_create_time is None:
        ct = Win32HandleTransferApi().get_process_create_time(expected_launcher_pid)
        assert ct is not None and ct > 0
        expected_launcher_create_time = ct

    script_file = tmp_path / f"wd_runner_{name}.py"
    ready_file = tmp_path / f"wd_ready_{name}.json"
    script_file.write_text(_WATCHDOG_SUBPROCESS_SCRIPT, encoding="utf-8")
    worker_id = f"wd-win-{name}-{uuid.uuid4().hex[:8]}"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(script_file),
            str(db_path),
            auth_token,
            str(ready_file),
            worker_id,
            str(expected_launcher_pid),
            str(expected_launcher_create_time),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if ready_file.exists():
            info = json.loads(ready_file.read_text(encoding="utf-8"))
            return proc, info
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=2)
            raise RuntimeError(f"Watchdog subprocess exited early: {err.decode('utf-8', 'replace')}")
        time.sleep(0.05)
    proc.kill()
    raise TimeoutError("Timed out waiting for Watchdog subprocess ready file")


def test_windows_cross_process_duplicate_handle_and_tree_termination(tmp_path: Path) -> None:
    """Real Windows 11 cross-process integration:
    1. Launcher creates Job Object A (with parent + child process tree) and Job Object B.
    2. Launcher duplicates Job Object A's handle into independent Watchdog process via DuplicateHandle.
    3. Watchdog verifies kernel object identity with CompareObjectHandles.
    4. Attempting termination with Job Object B's duplicated handle is BLOCKED (JOB_HANDLE_MISMATCH).
    5. Terminating with Job Object A's authorized duplicated handle terminates the full process tree.
    """
    from desktop_worker.watchdog.job_handle_transfer import (
        JobHandleTransferClient,
        duplicate_job_handle_to_process,
    )

    mgr = JobObjectProcessManager()
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess, sys, time; "
        f"p = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(60)"
    )
    launch_a = mgr.launch(f'"{sys.executable}" -c "{parent_code}"')
    launch_b = mgr.launch(f'"{sys.executable}" -c "import time; time.sleep(60)"')
    assert launch_a.ok and launch_a.process is not None
    assert launch_b.ok and launch_b.process is not None
    proc_a = launch_a.process
    proc_b = launch_b.process
    store = None
    wd_proc = None
    try:
        # Wait for child process in Job Object A
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            acct, _ = proc_a.query_basic_accounting()
            if acct is not None and acct.active_processes >= 2:
                break
            time.sleep(0.05)

        store, _outbox, ident_a = _seed_execution(
            tmp_path,
            execution_id="win-xproc-a",
            pid=proc_a.pid,
            create_time=proc_a.identity.process_creation_time or 0,
            job_object_name="dcf-win-xproc-a",
        )
        db_path = tmp_path / "win-xproc-a.db"
        auth_token = "test-secret-token-xproc"
        wd_proc, wd_info = _spawn_watchdog_subprocess(tmp_path, db_path, auth_token, "xproc1")

        client = JobHandleTransferClient(port=wd_info["transfer_port"], auth_token=auth_token)
        wd_pid, dup_handle_a1 = client.transfer_and_register(
            execution_id=ident_a.execution_id,
            source_job_handle=proc_a.identity.job_handle,
            attempt_id=ident_a.attempt_id,
            fencing_token=ident_a.fencing_token,
        )
        assert wd_pid == wd_info["pid"]
        assert dup_handle_a1 > 0

        # Duplicate a second handle to the SAME Job Object A into Watchdog and verify CompareObjectHandles == True
        dup_handle_a2 = duplicate_job_handle_to_process(proc_a.identity.job_handle, wd_pid)
        cmp_same = _send_ctrl(wd_info["ctrl_port"], {"op": "COMPARE", "h1": dup_handle_a1, "h2": dup_handle_a2})
        assert cmp_same["same"] is True

        # Duplicate a handle to DIFFERENT Job Object B into Watchdog and verify CompareObjectHandles == False
        dup_handle_b = duplicate_job_handle_to_process(proc_b.identity.job_handle, wd_pid)
        cmp_diff = _send_ctrl(wd_info["ctrl_port"], {"op": "COMPARE", "h1": dup_handle_a1, "h2": dup_handle_b})
        assert cmp_diff["same"] is False

        ident_dict = {
            "execution_id": ident_a.execution_id,
            "attempt_id": ident_a.attempt_id,
            "job_id": ident_a.job_id,
            "tenant_id": ident_a.tenant_id,
            "channel_id": ident_a.channel_id,
            "worker_id": ident_a.worker_id,
            "fencing_token": ident_a.fencing_token,
            "pid": ident_a.pid,
            "process_create_time": ident_a.process_create_time,
            "job_object_name": ident_a.job_object_name,
            "browser_user_data_dir": ident_a.browser_user_data_dir,
        }

        # Attempt termination with Job Object B's handle -> MUST BE BLOCKED
        blocked = _send_ctrl(
            wd_info["ctrl_port"],
            {"op": "TERMINATE", "identity": ident_dict, "job_handle": dup_handle_b, "event_id": "evt-xproc-blocked"},
        )
        assert blocked["error_code"] == "JOB_HANDLE_MISMATCH"
        assert blocked["verification_result"] == "INCONCLUSIVE"
        assert blocked["fully_completed"] is False
        assert blocked["evidence_persisted"] is False

        # Both Job Object A and B must still be alive
        acct_a, _ = proc_a.query_basic_accounting()
        acct_b, _ = proc_b.query_basic_accounting()
        assert acct_a is not None and acct_a.active_processes >= 2
        assert acct_b is not None and acct_b.active_processes == 1

        # Now terminate using Job Object A's second duplicated handle (different numeric handle, same kernel object!)
        ok_res = _send_ctrl(
            wd_info["ctrl_port"],
            {"op": "TERMINATE", "identity": ident_dict, "job_handle": dup_handle_a2, "event_id": "evt-xproc-ok"},
        )
        assert ok_res["verification_result"] == "TREE_TERMINATED"
        assert ok_res["fully_completed"] is True
        assert ok_res["active_processes_after"] == 0
        assert ok_res["pids_after"] == []
        assert ok_res["evidence_persisted"] is True
        assert ok_res["outbox_enqueued"] is True

        # Job Object B must remain untouched and alive
        acct_b_after, _ = proc_b.query_basic_accounting()
        assert acct_b_after is not None and acct_b_after.active_processes == 1

        _send_ctrl(wd_info["ctrl_port"], {"op": "SHUTDOWN"})
        wd_proc.wait(timeout=5)
    finally:
        if wd_proc is not None and wd_proc.poll() is None:
            wd_proc.kill()
        proc_a.terminate_job(timeout_ms=3000)
        proc_b.terminate_job(timeout_ms=3000)
        proc_a.close()
        proc_b.close()
        if store is not None:
            store.close()


def test_windows_watchdog_restart_blocks_until_reauthorized_and_no_handle_leak(tmp_path: Path) -> None:
    """Real Windows 11 Watchdog restart & handle-leak verification:
    1. Launcher transfers handle to Watchdog #1.
    2. Watchdog #1 shuts down (restart simulated) -> Watchdog #2 starts with empty registry.
    3. Termination attempt on Watchdog #2 without re-authorization is BLOCKED (JOB_HANDLE_NOT_AUTHORIZED).
    4. Launcher re-duplicates handle to Watchdog #2 -> termination succeeds.
    5. Repeated duplicate/register/unregister cycles show zero handle leak via GetProcessHandleCount.
    """
    from desktop_worker.watchdog.job_handle_transfer import (
        JobHandleTransferClient,
        Win32HandleTransferApi,
        duplicate_job_handle_in_process,
    )

    mgr = JobObjectProcessManager()
    launch = mgr.launch(f'"{sys.executable}" -c "import time; time.sleep(60)"')
    assert launch.ok and launch.process is not None
    proc = launch.process
    store = None
    wd1 = None
    wd2 = None
    try:
        store, _outbox, ident = _seed_execution(
            tmp_path,
            execution_id="win-restart",
            pid=proc.pid,
            create_time=proc.identity.process_creation_time or 0,
            job_object_name="dcf-win-restart",
        )
        db_path = tmp_path / "win-restart.db"
        auth_token = "test-secret-token-restart"

        # Watchdog #1 starts and receives handle
        wd1, info1 = _spawn_watchdog_subprocess(tmp_path, db_path, auth_token, "r1")
        client1 = JobHandleTransferClient(port=info1["transfer_port"], auth_token=auth_token)
        wd1_pid, dup1 = client1.transfer_and_register(
            execution_id=ident.execution_id,
            source_job_handle=proc.identity.job_handle,
            attempt_id=ident.attempt_id,
            fencing_token=ident.fencing_token,
        )
        assert wd1_pid == info1["pid"]

        # Shut down Watchdog #1 cleanly (simulating restart)
        _send_ctrl(info1["ctrl_port"], {"op": "SHUTDOWN"})
        wd1.wait(timeout=5)

        # Watchdog #2 starts fresh (new PID, empty in-memory authorization registry)
        wd2, info2 = _spawn_watchdog_subprocess(tmp_path, db_path, auth_token, "r2")
        assert info2["pid"] != info1["pid"]

        ident_dict = {
            "execution_id": ident.execution_id,
            "attempt_id": ident.attempt_id,
            "job_id": ident.job_id,
            "tenant_id": ident.tenant_id,
            "channel_id": ident.channel_id,
            "worker_id": ident.worker_id,
            "fencing_token": ident.fencing_token,
            "pid": ident.pid,
            "process_create_time": ident.process_create_time,
            "job_object_name": ident.job_object_name,
            "browser_user_data_dir": ident.browser_user_data_dir,
        }

        # Attempting termination on Watchdog #2 with stale handle number from Watchdog #1 -> BLOCKED fail-closed
        blocked = _send_ctrl(
            info2["ctrl_port"],
            {"op": "TERMINATE", "identity": ident_dict, "job_handle": dup1, "event_id": "evt-restart-blocked"},
        )
        assert blocked["error_code"] == "JOB_HANDLE_NOT_AUTHORIZED"
        assert blocked["verification_result"] == "INCONCLUSIVE"
        assert blocked["fully_completed"] is False
        assert blocked["evidence_persisted"] is False

        # Process must still be alive
        acct_alive, _ = proc.query_basic_accounting()
        assert acct_alive is not None and acct_alive.active_processes == 1

        # Re-establish authorization by duplicating handle into Watchdog #2
        client2 = JobHandleTransferClient(port=info2["transfer_port"], auth_token=auth_token)
        wd2_pid, dup2 = client2.transfer_and_register(
            execution_id=ident.execution_id,
            source_job_handle=proc.identity.job_handle,
            attempt_id=ident.attempt_id,
            fencing_token=ident.fencing_token,
        )
        assert wd2_pid == info2["pid"]

        # Now termination succeeds on Watchdog #2
        ok_res = _send_ctrl(
            info2["ctrl_port"],
            {"op": "TERMINATE", "identity": ident_dict, "job_handle": dup2, "event_id": "evt-restart-ok"},
        )
        assert ok_res["verification_result"] == "TREE_TERMINATED"
        assert ok_res["fully_completed"] is True
        assert ok_res["active_processes_after"] == 0

        _send_ctrl(info2["ctrl_port"], {"op": "SHUTDOWN"})
        wd2.wait(timeout=5)

        # Verify zero handle leaks across 25 duplicate + register(owned=True) + unregister cycles
        api = Win32HandleTransferApi()
        local_engine = SafeTerminationEngine(store)
        ok_before, count_before, _ = api.get_process_handle_count()
        assert ok_before is True
        import os
        for i in range(25):
            dup_local = duplicate_job_handle_in_process(proc.identity.job_handle, api=api)
            ok_acc, _msg = local_engine.accept_transferred_job_handle(
                f"leak-check-{i}",
                dup_local,
                attempt_id=f"att-leak-{i}",
                fencing_token=i + 1,
                source_pid=os.getpid(),
            )
            assert ok_acc is True
            assert local_engine.unregister_job_handle(f"leak-check-{i}", close_if_owned=True) is True
        local_engine.close()
        ok_after, count_after, _ = api.get_process_handle_count()
        assert ok_after is True
        assert count_after <= count_before + 2, (
            f"Handle leak detected: before={count_before}, after={count_after}"
        )
    finally:
        if wd1 is not None and wd1.poll() is None:
            wd1.kill()
        if wd2 is not None and wd2.poll() is None:
            wd2.kill()
        proc.terminate_job(timeout_ms=3000)
        proc.close()
        if store is not None:
            store.close()


def test_windows_watchdog_daemon_ipc_auth_spoof_replay_and_revocation(tmp_path: Path) -> None:
    """Real Windows 11 independent-process IPC security verification:
    1. Independent WatchdogDaemon starts its integrated JobHandleTransferServer.
    2. Unauthenticated client is rejected.
    3. Client spoofing source_pid is rejected via kernel GetExtendedTcpTable.
    4. Legitimate Launcher duplicates handle & registers; replay of same request is rejected.
    5. Revocation (UNREGISTER_HANDLE) blocks termination and closes owned handle.
    6. Re-registration allows termination of only the authorized tree.
    7. WatchdogDaemon shutdown stops the IPC server cleanly.
    """
    import os
    import socket

    from desktop_worker.watchdog.job_handle_transfer import (
        JobHandleTransferClient,
        duplicate_job_handle_to_process,
    )

    mgr = JobObjectProcessManager()
    launch = mgr.launch(f'"{sys.executable}" -c "import time; time.sleep(60)"')
    assert launch.ok and launch.process is not None
    proc = launch.process
    store = None
    wd_proc = None
    try:
        store, _outbox, ident = _seed_execution(
            tmp_path,
            execution_id="win-ipc-sec",
            pid=proc.pid,
            create_time=proc.identity.process_creation_time or 0,
            job_object_name="dcf-win-ipc-sec",
        )
        db_path = tmp_path / "win-ipc-sec.db"
        auth_token = "win11-real-hmac-secret-token"
        wd_proc, info = _spawn_watchdog_subprocess(tmp_path, db_path, auth_token, "ipcsec")
        assert info["ipc_running"] is True
        transfer_port = info["transfer_port"]

        # 1. Unauthenticated client rejected
        bad_client = JobHandleTransferClient(port=transfer_port, auth_token="invalid-token")
        resp_unauth = bad_client._send({"op": "PING"})
        assert resp_unauth["ok"] is False
        assert resp_unauth["error"] == "unauthorized"

        # 2. Spoofed source_pid rejected by real Windows kernel GetExtendedTcpTable
        client = JobHandleTransferClient(port=transfer_port, auth_token=auth_token)
        resp_spoof = client._send(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": 1234,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": os.getpid() + 7777,
            }
        )
        assert resp_spoof["ok"] is False
        assert resp_spoof["error"] == "spoofed_source_pid"

        # 3. Legitimate DuplicateHandle + signed REGISTER_HANDLE + replay rejection
        ping = client.ping_watchdog()
        assert ping.watchdog_pid == info["pid"]
        assert ping.daemon_instance_id == info["daemon_instance_id"]
        dup_h = duplicate_job_handle_to_process(
            proc.identity.job_handle,
            ping.watchdog_pid,
            expected_target_create_time=ping.watchdog_create_time,
        )
        signed_reg = client._build_signed_request(
            {
                "op": "REGISTER_HANDLE",
                "execution_id": ident.execution_id,
                "job_handle": dup_h,
                "attempt_id": ident.attempt_id,
                "fencing_token": ident.fencing_token,
                "source_pid": os.getpid(),
                "target_watchdog_pid": ping.watchdog_pid,
                "target_watchdog_create_time": ping.watchdog_create_time,
                "target_daemon_instance_id": ping.daemon_instance_id,
            }
        )
        resp_ok = client._send_raw(signed_reg)
        assert resp_ok["ok"] is True

        # Replay of exact same signed REGISTER_HANDLE -> REJECTED
        resp_replay = client._send_raw(signed_reg)
        assert resp_replay["ok"] is False
        assert resp_replay["error"] == "replay_detected"

        # 4. Revocation blocks termination
        assert client.unregister(ident.execution_id) is True
        ident_dict = {
            "execution_id": ident.execution_id,
            "attempt_id": ident.attempt_id,
            "job_id": ident.job_id,
            "tenant_id": ident.tenant_id,
            "channel_id": ident.channel_id,
            "worker_id": ident.worker_id,
            "fencing_token": ident.fencing_token,
            "pid": ident.pid,
            "process_create_time": ident.process_create_time,
            "job_object_name": ident.job_object_name,
            "browser_user_data_dir": ident.browser_user_data_dir,
        }
        blocked = _send_ctrl(
            info["ctrl_port"],
            {"op": "TERMINATE", "identity": ident_dict, "job_handle": dup_h, "event_id": "evt-revoked"},
        )
        assert blocked["error_code"] == "JOB_HANDLE_NOT_AUTHORIZED"
        assert blocked["verification_result"] == "INCONCLUSIVE"
        assert blocked["evidence_persisted"] is False

        # 5. Re-transfer & terminate cleanly
        wd_pid2, dup_h2 = client.transfer_and_register(
            execution_id=ident.execution_id,
            source_job_handle=proc.identity.job_handle,
            attempt_id=ident.attempt_id,
            fencing_token=ident.fencing_token,
        )
        assert wd_pid2 == info["pid"]
        term_ok = _send_ctrl(
            info["ctrl_port"],
            {"op": "TERMINATE", "identity": ident_dict, "job_handle": dup_h2, "event_id": "evt-ipcsec-ok"},
        )
        assert term_ok["verification_result"] == "TREE_TERMINATED"
        assert term_ok["fully_completed"] is True
        assert term_ok["active_processes_after"] == 0

        # 6. WatchdogDaemon shuts down and closes IPC server
        _send_ctrl(info["ctrl_port"], {"op": "SHUTDOWN"})
        wd_proc.wait(timeout=5)
        with pytest.raises(OSError):
            with socket.create_connection(("127.0.0.1", transfer_port), timeout=0.5):
                pass
    finally:
        if wd_proc is not None and wd_proc.poll() is None:
            wd_proc.kill()
        proc.terminate_job(timeout_ms=3000)
        proc.close()
        if store is not None:
            store.close()
