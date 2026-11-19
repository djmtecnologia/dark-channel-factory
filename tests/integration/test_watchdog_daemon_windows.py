"""Real Windows integration tests for WatchdogDaemon (Sprint 2.7 - Task 5).

Exercises real Win32 Named Mutexes (`CreateMutexW`, `WaitForSingleObject`,
`ReleaseMutex`, `CloseHandle`), real multi-process contention, `WAIT_ABANDONED`
recovery after abrupt process crash, parent-process independence, controlled
shutdown, and verification that no external processes are terminated.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from desktop_worker.watchdog.identity import CanonicalExecutionIdentity
from desktop_worker.watchdog.state_store import (
    AttemptHubStatus,
    ExecutionStatus,
    StateStore,
)
from desktop_worker.watchdog.watchdog_daemon import (
    EXIT_ALREADY_RUNNING,
    EXIT_CLEAN,
    MonitoringStatus,
    MutexAcquireStatus,
    WatchdogAlreadyRunningError,
    WatchdogConfig,
    WatchdogDaemon,
    format_utc_iso,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only integration tests for Win32 Named Mutex and detached daemon",
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _unique_worker_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _is_pid_alive(pid: int) -> bool:
    """Check whether a Windows PID is still running via OpenProcess + GetExitCodeProcess."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return int(code.value) == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _terminate_pid_for_test_cleanup(pid: int) -> None:
    """Test-harness-only cleanup helper to terminate a spawned test process."""
    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if handle:
        try:
            kernel32.TerminateProcess(handle, 1)
        finally:
            kernel32.CloseHandle(handle)


def _wait_until(predicate, timeout_sec: float = 10.0, interval_sec: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_sec)
    return False


def _is_mutex_held(mutex_name: str) -> bool:
    """Return True if another process currently holds the named Win32 Mutex."""
    from desktop_worker.watchdog.watchdog_daemon import WindowsNamedMutex

    probe = WindowsNamedMutex(mutex_name)
    res = probe.acquire(timeout_ms=0)
    if res.acquired:
        probe.release()
        return False
    return res.status == MutexAcquireStatus.ALREADY_HELD


# ---------------------------------------------------------------------------
# Tests 1, 2, 3: Single instance & second instance blocked by real Win32 Mutex
# ---------------------------------------------------------------------------


def test_windows_mutex_blocks_concurrent_daemon_instance(tmp_path: Path) -> None:
    worker_id = _unique_worker_id("win_mutex")
    db_path = tmp_path / "watchdog.db"
    StateStore(db_path).close()
    cfg = WatchdogConfig(
        worker_id=worker_id,
        db_path=db_path,
        poll_interval_sec=0.1,
        heartbeat_interval_sec=5.0,
        heartbeat_timeout_sec=30.0,
    )

    # Start first real daemon process via CLI
    proc1 = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "desktop_worker.watchdog.watchdog_daemon",
            "--worker-id",
            worker_id,
            "--db-path",
            str(db_path),
            "--poll-interval",
            "0.1",
            "--heartbeat-interval",
            "5.0",
            "--heartbeat-timeout",
            "30.0",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Wait until proc1 has acquired the Win32 Named Mutex
        assert _wait_until(lambda: _is_mutex_held(cfg.mutex_name), timeout_sec=10.0), (
            f"First daemon did not acquire Mutex; poll={proc1.poll()}"
        )

        # Second instance in-process must fail with WatchdogAlreadyRunningError
        daemon2 = WatchdogDaemon(cfg)
        with pytest.raises(WatchdogAlreadyRunningError):
            daemon2.start()
        assert daemon2.is_running() is False

        # Second instance via CLI must exit with EXIT_ALREADY_RUNNING (2)
        proc2 = subprocess.run(
            [
                sys.executable,
                "-m",
                "desktop_worker.watchdog.watchdog_daemon",
                "--worker-id",
                worker_id,
                "--db-path",
                str(db_path),
                "--poll-interval",
                "0.1",
                "--heartbeat-interval",
                "5.0",
                "--heartbeat-timeout",
                "30.0",
                "--max-cycles",
                "1",
            ],
            cwd=str(REPO_ROOT),
            timeout=15.0,
            check=False,
        )
        assert proc2.returncode == EXIT_ALREADY_RUNNING
    finally:
        proc1.kill()
        proc1.wait(timeout=10.0)


# ---------------------------------------------------------------------------
# Tests 4, 5, 6, 7: Abrupt crash -> WAIT_ABANDONED + State Store validation
# ---------------------------------------------------------------------------


def test_windows_wait_abandoned_and_state_store_validation(tmp_path: Path) -> None:
    worker_id = _unique_worker_id("win_abandoned")
    db_path = tmp_path / "watchdog_abandoned.db"
    StateStore(db_path).close()
    cfg = WatchdogConfig(
        worker_id=worker_id,
        db_path=db_path,
        poll_interval_sec=0.1,
        heartbeat_interval_sec=5.0,
        heartbeat_timeout_sec=30.0,
    )

    # Start first daemon process
    proc1 = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "desktop_worker.watchdog.watchdog_daemon",
            "--worker-id",
            worker_id,
            "--db-path",
            str(db_path),
            "--poll-interval",
            "0.2",
            "--heartbeat-interval",
            "5.0",
            "--heartbeat-timeout",
            "30.0",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert _wait_until(lambda: _is_mutex_held(cfg.mutex_name), timeout_sec=10.0)

    # Open a non-owning reference handle to the named mutex so the Windows kernel
    # keeps the mutex object alive across proc1's abrupt death, allowing the kernel
    # to deliver genuine WAIT_ABANDONED (0x80) to the recovering instance.
    from desktop_worker.watchdog.watchdog_daemon import Win32MutexApi

    ref_api = Win32MutexApi()
    ref_handle, _ = ref_api.create_mutex(cfg.mutex_name)
    assert ref_handle != 0

    try:
        # Abruptly kill first daemon (TerminateProcess) without releasing Mutex
        proc1.kill()
        proc1.wait(timeout=10.0)

        # Corrupt a record in State Store before second instance starts to verify validation
        now_iso = format_utc_iso(datetime.now(timezone.utc))
        identity = CanonicalExecutionIdentity(
            execution_id="exec-abandoned-1",
            attempt_id="att-abandoned-1",
            job_id="job-abandoned-1",
            tenant_id="tenant-1",
            channel_id="channel-1",
            worker_id=worker_id,
            fencing_token=1,
            pid=12345,
            process_create_time=133500000000000000,
            job_object_name="Local\\DCF_UPLOAD_JOB_abandoned",
            browser_user_data_dir=str(tmp_path / "profile"),
        )
        with StateStore(db_path) as store:
            store.record_execution(identity, boot_time_utc=now_iso, status=ExecutionStatus.RUNNING)

        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE upload_execution SET checksum_sha256 = 'tampered_checksum';")
        conn.commit()
        conn.close()

        # Start second daemon -> must receive WAIT_ABANDONED and classify corrupted store as INCONCLUSIVE
        daemon2 = WatchdogDaemon(cfg)
        acquire_res = daemon2.start()
        try:
            assert acquire_res.status == MutexAcquireStatus.ABANDONED
            cycle = daemon2.run_once()
            assert cycle.abandoned_mutex_recovery is True
            assert cycle.overall_status == MonitoringStatus.INCONCLUSIVE
            assert cycle.integrity_failures >= 1
        finally:
            assert daemon2.stop() is True
    finally:
        ref_api.close_handle(ref_handle)


# ---------------------------------------------------------------------------
# Test 8: Independence from parent process (simulating desktop_node.py crash)
# ---------------------------------------------------------------------------


def test_windows_daemon_survives_parent_process_termination(tmp_path: Path) -> None:
    worker_id = _unique_worker_id("win_indep")
    db_path = tmp_path / "watchdog_indep.db"
    StateStore(db_path).close()
    go_file = tmp_path / "go_indep.txt"
    pid_file = tmp_path / "daemon_pid.txt"
    mutex_name = f"Local\\DCF_WATCHDOG_LOCK_{worker_id}"

    # Parent script simulates desktop_node.py spawning the detached watchdog daemon
    # inside a parent Job Object that permits CREATE_BREAKAWAY_FROM_JOB.
    parent_script = f"""
import time
from pathlib import Path
from desktop_worker.watchdog.watchdog_daemon import WatchdogConfig, spawn_detached_watchdog_daemon

go_file = Path({str(go_file)!r})
while not go_file.exists():
    time.sleep(0.02)

cfg = WatchdogConfig(
    worker_id={worker_id!r},
    db_path={str(db_path)!r},
    poll_interval_sec=0.1,
    heartbeat_interval_sec=5.0,
    heartbeat_timeout_sec=30.0,
)
child = spawn_detached_watchdog_daemon(cfg, cwd={str(REPO_ROOT)!r})
Path({str(pid_file)!r}).write_text(str(child.pid), encoding="utf-8")
# Keep parent alive briefly until test kills parent abruptly
time.sleep(30)
"""
    job_handle = _create_test_job_object(allow_breakaway=True)
    parent_proc = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    daemon_pid: int | None = None
    try:
        _assign_pid_to_job(job_handle, parent_proc.pid)
        go_file.write_text("go", encoding="utf-8")
        assert _wait_until(
            lambda: pid_file.exists() and _is_mutex_held(mutex_name),
            timeout_sec=12.0,
        )
        daemon_pid = int(pid_file.read_text(encoding="utf-8").strip())
        assert _is_pid_alive(daemon_pid)

        # Abruptly terminate parent process (simulating desktop_node.py crash)
        parent_proc.kill()
        parent_proc.wait(timeout=10.0)
        assert not _is_pid_alive(parent_proc.pid)

        # Confirm daemon is STILL alive and STILL holds the Mutex
        time.sleep(0.5)
        assert _is_pid_alive(daemon_pid), "WatchdogDaemon died when parent process was killed"

        cfg = WatchdogConfig(
            worker_id=worker_id,
            db_path=db_path,
            poll_interval_sec=0.1,
            heartbeat_interval_sec=5.0,
            heartbeat_timeout_sec=30.0,
        )
        probe = WatchdogDaemon(cfg)
        with pytest.raises(WatchdogAlreadyRunningError):
            probe.start()
    finally:
        _terminate_and_close_job(job_handle)
        if parent_proc.poll() is None:
            parent_proc.kill()
            parent_proc.wait(timeout=5.0)
        if daemon_pid is not None and _is_pid_alive(daemon_pid):
            _terminate_pid_for_test_cleanup(daemon_pid)


# ---------------------------------------------------------------------------
# Tests 9 & 10: Controlled shutdown & external harmless process untouched
# ---------------------------------------------------------------------------


def test_windows_controlled_shutdown_and_no_external_process_killed(tmp_path: Path) -> None:
    worker_id = _unique_worker_id("win_shutdown")
    db_path = tmp_path / "watchdog_shutdown.db"

    # Spawn a harmless external process
    harmless = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _is_pid_alive(harmless.pid)

        # Seed an expired-heartbeat execution pointing to the harmless PID
        now = datetime.now(timezone.utc)
        stale_hb = format_utc_iso(now - timedelta(seconds=600))
        identity = CanonicalExecutionIdentity(
            execution_id="exec-ext-1",
            attempt_id="att-ext-1",
            job_id="job-ext-1",
            tenant_id="tenant-1",
            channel_id="channel-1",
            worker_id=worker_id,
            fencing_token=1,
            pid=harmless.pid,
            process_create_time=133500000000000000,
            job_object_name="Local\\DCF_UPLOAD_JOB_ext",
            browser_user_data_dir=str(tmp_path / "profile-ext"),
        )
        with StateStore(db_path) as store:
            store.record_execution(identity, boot_time_utc=format_utc_iso(now))
            store.upsert_attempt(
                attempt_id="att-ext-1",
                job_id="job-ext-1",
                fencing_token=1,
                hub_status=AttemptHubStatus.IN_PROGRESS,
                last_heartbeat_at_utc=stale_hb,
            )

        cfg = WatchdogConfig(
            worker_id=worker_id,
            db_path=db_path,
            poll_interval_sec=0.05,
            heartbeat_interval_sec=5.0,
            heartbeat_timeout_sec=30.0,
        )
        daemon = WatchdogDaemon(cfg)
        exit_code = daemon.run(max_cycles=2, install_signals=False)

        assert exit_code == EXIT_CLEAN
        assert daemon.health_check().mutex_acquired is False
        assert daemon.health_check().last_cycle_status == MonitoringStatus.HEARTBEAT_EXPIRED

        # Harmless external process MUST still be alive
        assert _is_pid_alive(harmless.pid)

        # Mutex must have been released cleanly so a new instance can acquire WAIT_OBJECT_0
        daemon_next = WatchdogDaemon(cfg)
        res = daemon_next.start()
        try:
            assert res.status == MutexAcquireStatus.ACQUIRED
        finally:
            assert daemon_next.stop() is True
    finally:
        harmless.kill()
        harmless.wait(timeout=5.0)


# ---------------------------------------------------------------------------
# Correction 1 (Real Windows): stop() timeout keeps real Win32 Mutex held
# ---------------------------------------------------------------------------


def test_windows_stop_timeout_keeps_real_mutex_held_until_cycle_finishes(
    tmp_path: Path,
) -> None:
    import threading

    worker_id = _unique_worker_id("win_stop_lock")
    db_path = tmp_path / "watchdog_stop_lock.db"
    StateStore(db_path).close()

    cfg = WatchdogConfig(
        worker_id=worker_id,
        db_path=db_path,
        poll_interval_sec=0.1,
        heartbeat_interval_sec=5.0,
        heartbeat_timeout_sec=30.0,
        stop_timeout_sec=0.1,
    )
    daemon1 = WatchdogDaemon(cfg)
    daemon1.start()
    assert daemon1._store is not None

    cycle_entered = threading.Event()
    release_cycle = threading.Event()
    orig_verify = daemon1._store.verify_integrity

    def blocking_verify():
        cycle_entered.set()
        release_cycle.wait(timeout=5.0)
        return orig_verify()

    daemon1._store.verify_integrity = blocking_verify  # type: ignore[method-assign]

    worker = threading.Thread(target=daemon1.run_once, daemon=True)
    worker.start()
    assert cycle_entered.wait(timeout=3.0)

    try:
        # stop() times out while cycle is active -> must return False and keep Mutex & Store
        assert daemon1.stop(timeout_sec=0.1) is False
        assert daemon1._store is not None
        assert daemon1._mutex.is_owned is True

        # A second real Windows process MUST be blocked by the Win32 Named Mutex
        proc2 = subprocess.run(
            [
                sys.executable,
                "-m",
                "desktop_worker.watchdog.watchdog_daemon",
                "--worker-id",
                worker_id,
                "--db-path",
                str(db_path),
                "--poll-interval",
                "0.1",
                "--heartbeat-interval",
                "5.0",
                "--heartbeat-timeout",
                "30.0",
                "--max-cycles",
                "1",
            ],
            cwd=str(REPO_ROOT),
            timeout=15.0,
            check=False,
        )
        assert proc2.returncode == EXIT_ALREADY_RUNNING
    finally:
        release_cycle.set()
        worker.join(timeout=5.0)
        assert not worker.is_alive()

    # Subsequent stop() on the owning thread releases Store and Win32 Mutex cleanly
    assert daemon1.stop(timeout_sec=3.0) is True
    assert daemon1._store is None
    assert daemon1._mutex.is_owned is False
    assert _is_mutex_held(cfg.mutex_name) is False


# ---------------------------------------------------------------------------
# Correction 2 (Real Windows): Parent Job Object Breakaway Allowed vs Denied
# ---------------------------------------------------------------------------


def _create_test_job_object(*, allow_breakaway: bool) -> int:
    """Create a real Windows Job Object with KILL_ON_JOB_CLOSE (and optional BREAKAWAY_OK).

    Used ONLY in this test harness to simulate a parent process inside a Job Object.
    Does NOT modify desktop_worker/watchdog/job_object.py.
    """
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
    JobObjectExtendedLimitInformation = 9

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    job_handle = kernel32.CreateJobObjectW(None, None)
    assert job_handle, f"CreateJobObjectW failed: {ctypes.get_last_error()}"

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if allow_breakaway:
        flags |= JOB_OBJECT_LIMIT_BREAKAWAY_OK
    info.BasicLimitInformation.LimitFlags = flags

    ok = kernel32.SetInformationJobObject(
        job_handle,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        err = ctypes.get_last_error()
        kernel32.CloseHandle(job_handle)
        raise AssertionError(f"SetInformationJobObject failed: {err}")
    return int(job_handle)


def _assign_pid_to_job(job_handle: int, pid: int) -> None:
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    proc_handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
    assert proc_handle, f"OpenProcess failed: {ctypes.get_last_error()}"
    try:
        ok = kernel32.AssignProcessToJobObject(wintypes.HANDLE(job_handle), proc_handle)
        assert ok, f"AssignProcessToJobObject failed: {ctypes.get_last_error()}"
    finally:
        kernel32.CloseHandle(proc_handle)


def _terminate_and_close_job(job_handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    try:
        kernel32.TerminateJobObject(wintypes.HANDLE(job_handle), 1)
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(job_handle))


def test_windows_daemon_survives_parent_job_object_when_breakaway_allowed(
    tmp_path: Path,
) -> None:
    worker_id = _unique_worker_id("win_job_ok")
    db_path = tmp_path / "watchdog_job_ok.db"
    StateStore(db_path).close()
    go_file = tmp_path / "go.txt"
    pid_file = tmp_path / "daemon_pid.txt"
    mutex_name = f"Local\\DCF_WATCHDOG_LOCK_{worker_id}"

    # Parent waits until assigned to Job Object, then spawns detached watchdog daemon
    parent_script = f"""
import time
from pathlib import Path
from desktop_worker.watchdog.watchdog_daemon import WatchdogConfig, spawn_detached_watchdog_daemon

go_file = Path({str(go_file)!r})
while not go_file.exists():
    time.sleep(0.02)

cfg = WatchdogConfig(
    worker_id={worker_id!r},
    db_path={str(db_path)!r},
    poll_interval_sec=0.1,
    heartbeat_interval_sec=5.0,
    heartbeat_timeout_sec=30.0,
)
child = spawn_detached_watchdog_daemon(cfg, cwd={str(REPO_ROOT)!r})
Path({str(pid_file)!r}).write_text(str(child.pid), encoding="utf-8")
time.sleep(30)
"""
    job_handle = _create_test_job_object(allow_breakaway=True)
    parent_proc = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    daemon_pid: int | None = None
    job_closed = False
    try:
        _assign_pid_to_job(job_handle, parent_proc.pid)
        go_file.write_text("go", encoding="utf-8")

        assert _wait_until(
            lambda: pid_file.exists() and _is_mutex_held(mutex_name),
            timeout_sec=12.0,
        )
        daemon_pid = int(pid_file.read_text(encoding="utf-8").strip())
        assert _is_pid_alive(daemon_pid)

        # Terminate parent's Job Object (kills parent and any non-broken-away descendants)
        _terminate_and_close_job(job_handle)
        job_closed = True
        parent_proc.wait(timeout=10.0)
        assert not _is_pid_alive(parent_proc.pid)

        # Watchdog daemon MUST survive because CREATE_BREAKAWAY_FROM_JOB succeeded
        time.sleep(0.5)
        assert _is_pid_alive(daemon_pid), "Daemon died with parent Job Object despite BREAKAWAY_OK"
        assert _is_mutex_held(mutex_name) is True
    finally:
        if not job_closed:
            _terminate_and_close_job(job_handle)
        if daemon_pid is not None and _is_pid_alive(daemon_pid):
            _terminate_pid_for_test_cleanup(daemon_pid)


def test_windows_daemon_spawn_fails_explicitly_when_parent_job_denies_breakaway(
    tmp_path: Path,
) -> None:
    worker_id = _unique_worker_id("win_job_deny")
    db_path = tmp_path / "watchdog_job_deny.db"
    StateStore(db_path).close()
    go_file = tmp_path / "go.txt"
    result_file = tmp_path / "result.txt"
    mutex_name = f"Local\\DCF_WATCHDOG_LOCK_{worker_id}"

    # Spawn a harmless external process to confirm nothing external is terminated
    harmless = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    parent_script = f"""
import time
from pathlib import Path
from desktop_worker.watchdog.watchdog_daemon import (
    WatchdogConfig,
    WatchdogSpawnError,
    spawn_detached_watchdog_daemon,
)

go_file = Path({str(go_file)!r})
while not go_file.exists():
    time.sleep(0.02)

cfg = WatchdogConfig(
    worker_id={worker_id!r},
    db_path={str(db_path)!r},
    poll_interval_sec=0.1,
    heartbeat_interval_sec=5.0,
    heartbeat_timeout_sec=30.0,
)
try:
    child = spawn_detached_watchdog_daemon(cfg, cwd={str(REPO_ROOT)!r})
    Path({str(result_file)!r}).write_text(f"UNEXPECTED_SPAWN:{{child.pid}}", encoding="utf-8")
except WatchdogSpawnError as exc:
    Path({str(result_file)!r}).write_text(f"SPAWN_ERROR:{{exc}}", encoding="utf-8")
"""
    job_handle = _create_test_job_object(allow_breakaway=False)
    parent_proc = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _assign_pid_to_job(job_handle, parent_proc.pid)
        go_file.write_text("go", encoding="utf-8")

        assert _wait_until(lambda: result_file.exists(), timeout_sec=12.0)
        parent_proc.wait(timeout=10.0)

        outcome = result_file.read_text(encoding="utf-8")
        assert outcome.startswith("SPAWN_ERROR:"), f"Expected WatchdogSpawnError, got: {outcome}"
        assert "CREATE_BREAKAWAY_FROM_JOB" in outcome
        assert "ERROR_ACCESS_DENIED" in outcome
        assert _is_mutex_held(mutex_name) is False
        assert _is_pid_alive(harmless.pid) is True
    finally:
        _terminate_and_close_job(job_handle)
        if harmless.poll() is None:
            harmless.kill()
            harmless.wait(timeout=5.0)
