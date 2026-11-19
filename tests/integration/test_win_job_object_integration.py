"""Windows integration tests for Job Object Process Manager.

Uses only a harmless Python sleep process. Skipped automatically on non-Windows.
"""

from __future__ import annotations

import sys

import pytest

from desktop_worker.watchdog.job_object import (
    JobObjectProcessManager,
    LauncherState,
    TerminationStatus,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only integration test (requires Win32 Job Objects).",
)


def test_windows_job_object_lifecycle_integration():
    mgr = JobObjectProcessManager()
    cmd = f'"{sys.executable}" -c "import time; time.sleep(30)"'

    res = mgr.launch(cmd)
    assert res.ok is True, f"Launch failed: {res.error}"
    assert res.process is not None

    proc = res.process
    try:
        assert proc.state == LauncherState.RUNNING
        assert proc.pid > 0
        assert proc.verify_identity() is True

        acct, acct_err = proc.query_basic_accounting()
        assert acct_err is None
        assert acct is not None
        assert acct.active_processes >= 1

        pid_list = proc.query_process_id_list()
        assert pid_list.ok is True
        assert pid_list.truncated is False
        assert proc.pid in pid_list.pids

        term = proc.terminate_job(timeout_ms=5000, poll_interval_ms=50)
        assert term.status == TerminationStatus.TREE_TERMINATED, term.reason
        assert term.active_processes == 0
        assert term.pids == []
        assert proc.state == LauncherState.TERMINATED
    finally:
        proc.close()
        assert proc.is_closed is True


def test_windows_job_object_process_tree_termination_integration(tmp_path):
    """Verify TerminateJobObject terminates both parent and child processes."""
    import ctypes
    from ctypes import wintypes
    import time

    from desktop_worker.watchdog.job_object import WAIT_OBJECT_0

    SYNCHRONIZE = 0x00100000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    child_pid_file = tmp_path / "child_pid.txt"
    parent_script = (
        "import subprocess, sys, time; "
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        f"open(r'{child_pid_file}', 'w').write(str(p.pid)); "
        "time.sleep(30)"
    )
    cmd = f'"{sys.executable}" -c "{parent_script}"'

    mgr = JobObjectProcessManager()
    res = mgr.launch(cmd)
    assert res.ok is True, f"Launch failed: {res.error}"
    assert res.process is not None

    proc = res.process
    try:
        # Wait for child process to spawn and register in the Job Object
        deadline = time.monotonic() + 5.0
        child_pid = None
        while time.monotonic() < deadline:
            if child_pid_file.exists():
                content = child_pid_file.read_text().strip()
                if content.isdigit():
                    child_pid = int(content)
                    acct, _ = proc.query_basic_accounting()
                    if acct and acct.active_processes >= 2:
                        break
            time.sleep(0.05)

        assert child_pid is not None, "Child process did not start in time."
        pid_list = proc.query_process_id_list()
        assert pid_list.ok is True
        assert proc.pid in pid_list.pids
        assert child_pid in pid_list.pids

        # Obtain a SYNCHRONIZE handle for the child process before termination
        # so we can wait on it reliably even after TerminateJobObject.
        child_handle = kernel32.OpenProcess(SYNCHRONIZE, False, child_pid)
        assert child_handle, f"OpenProcess failed for child PID {child_pid}: {ctypes.get_last_error()}"

        try:
            term = proc.terminate_job(timeout_ms=5000, poll_interval_ms=50)
            assert term.status == TerminationStatus.TREE_TERMINATED, term.reason
            assert term.active_processes == 0
            assert term.pids == []

            # Confirm parent termination via its existing process_handle
            parent_handle = proc.identity.process_handle
            assert parent_handle is not None
            parent_wait = kernel32.WaitForSingleObject(wintypes.HANDLE(parent_handle), 5000)
            assert parent_wait == WAIT_OBJECT_0, f"Parent process did not signal termination (wait={parent_wait})"

            # Confirm child termination via its SYNCHRONIZE handle
            child_wait = kernel32.WaitForSingleObject(child_handle, 5000)
            assert child_wait == WAIT_OBJECT_0, f"Child process did not signal termination (wait={child_wait})"
        finally:
            closed = kernel32.CloseHandle(child_handle)
            assert closed, (
                f"CloseHandle failed for child PID {child_pid}: "
                f"{ctypes.get_last_error()}"
            )
    finally:
        proc.close()
        assert proc.is_closed is True
