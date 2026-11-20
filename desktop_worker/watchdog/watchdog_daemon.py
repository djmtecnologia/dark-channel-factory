"""Independent Local Watchdog Daemon for Windows (Sprint 2.7 - Task 5).

Executes the local watchdog monitoring lifecycle as an independent process
from `desktop_node.py`, guarded by a Windows Named Mutex
(`Local\\DCF_WATCHDOG_LOCK_<worker_id>`).

Security boundaries (Task 5):
- Does NOT execute TerminateJobObject or TerminateProcess on upload processes.
- Does NOT terminate processes by name.
- Does NOT launch uploads, create retries, mutate fencing_token, or release jobs.
- Does NOT mutate execution status merely because a heartbeat expired.
- Does NOT deliver Outbox events to Cloud Hub or mark events delivered.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import logging
import math
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple, Union

from desktop_worker.watchdog.job_handle_transfer import (
    JobHandleTransferServer,
)
from desktop_worker.watchdog.outbox import (
    OUTBOX_SCHEMA_VERSION,
    OutboxEventRecord,
    OutboxEventStatus,
    _OUTBOX_REQUIRED_INDEX_SPECS,
    _OUTBOX_SCHEMA_META_KEY,
    _OUTBOX_V2_REQUIRED_COLUMNS,
    _outbox_checksum_payload,
)
from desktop_worker.watchdog.state_store import (
    SCHEMA_VERSION as STATE_STORE_SCHEMA_VERSION,
    AttemptRecord,
    ExecutionRecord,
    ExecutionStatus,
    IntegrityStatus,
    ProcessIdentityRecord,
    StateStore,
    StateStoreCorruptionError,
    StateStoreError,
    StateStorePersistenceError,
    StateStoreSchemaError,
    compute_canonical_checksum,
)

logger = logging.getLogger(__name__)

WATCHDOG_VERSION = "2.7.5"

# Win32 Mutex & Wait constants
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5

# Win32 Process Creation flags (for independent daemon spawn only)
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000

# Exit codes
EXIT_CLEAN = 0
EXIT_FATAL_ERROR = 1
EXIT_ALREADY_RUNNING = 2
EXIT_UNCLEAN_SHUTDOWN = 3


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def format_utc_iso(dt: datetime) -> str:
    """Format a datetime as UTC ISO-8601 string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_utc_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime."""
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Enums & Exceptions
# ---------------------------------------------------------------------------


class MutexAcquireStatus(str, Enum):
    """Result of attempting to acquire the Windows Named Mutex."""

    ACQUIRED = "ACQUIRED"
    ABANDONED = "ABANDONED"
    ALREADY_HELD = "ALREADY_HELD"
    FAILED = "FAILED"


class MonitoringStatus(str, Enum):
    """Classification of a monitoring cycle or execution observation."""

    HEALTHY = "HEALTHY"
    HEARTBEAT_EXPIRED = "HEARTBEAT_EXPIRED"
    INCONCLUSIVE = "INCONCLUSIVE"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"


class WatchdogDaemonError(RuntimeError):
    """Base exception for WatchdogDaemon failures."""


class WatchdogConfigError(WatchdogDaemonError):
    """Raised when daemon configuration parameters are invalid."""


class WatchdogMutexError(WatchdogDaemonError):
    """Raised when the Windows Named Mutex cannot be acquired."""


class WatchdogAlreadyRunningError(WatchdogMutexError):
    """Raised when another WatchdogDaemon instance already holds the Mutex."""


class WatchdogSpawnError(WatchdogDaemonError):
    """Raised when spawning an independent detached daemon process fails."""


class WatchdogIPCError(WatchdogDaemonError):
    """Raised when the Watchdog IPC handle transfer server fails or is unavailable."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _validate_positive_finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WatchdogConfigError(f"{name} must be a positive finite number, got {value!r}")
    fval = float(value)
    if not math.isfinite(fval) or fval <= 0.0:
        raise WatchdogConfigError(f"{name} must be > 0 and finite, got {value!r}")
    return fval


@dataclass(frozen=True)
class WatchdogConfig:
    """Validated configuration for WatchdogDaemon.

    Operational polling/heartbeat intervals are required explicitly and are not
    hardcoded to uncalibrated defaults.
    """

    worker_id: str
    db_path: Union[str, Path]
    poll_interval_sec: float
    heartbeat_interval_sec: float
    heartbeat_timeout_sec: float
    stop_timeout_sec: float = 10.0
    check_outbox: bool = True
    watchdog_version: str = WATCHDOG_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.worker_id, str) or not self.worker_id.strip():
            raise WatchdogConfigError("worker_id must be a non-empty string")
        if "\\" in self.worker_id or "/" in self.worker_id:
            raise WatchdogConfigError("worker_id must not contain path separators")
        if not self.db_path or not str(self.db_path).strip():
            raise WatchdogConfigError("db_path must be a non-empty path")

        poll = _validate_positive_finite("WATCHDOG_POLL_INTERVAL_SEC", self.poll_interval_sec)
        hb_interval = _validate_positive_finite(
            "WATCHDOG_HEARTBEAT_INTERVAL_SEC", self.heartbeat_interval_sec
        )
        hb_timeout = _validate_positive_finite(
            "WATCHDOG_HEARTBEAT_TIMEOUT_SEC", self.heartbeat_timeout_sec
        )
        _validate_positive_finite("stop_timeout_sec", self.stop_timeout_sec)

        if hb_timeout < hb_interval:
            raise WatchdogConfigError(
                f"WATCHDOG_HEARTBEAT_TIMEOUT_SEC ({hb_timeout}) must be >= "
                f"WATCHDOG_HEARTBEAT_INTERVAL_SEC ({hb_interval})"
            )
        if poll <= 0.0:
            raise WatchdogConfigError("WATCHDOG_POLL_INTERVAL_SEC must be > 0")

    @property
    def mutex_name(self) -> str:
        return f"Local\\DCF_WATCHDOG_LOCK_{self.worker_id.strip()}"

    @classmethod
    def from_env(
        cls,
        *,
        worker_id: str,
        db_path: Union[str, Path],
        env: Optional[Dict[str, str]] = None,
    ) -> "WatchdogConfig":
        """Build and validate WatchdogConfig from environment variables."""
        source = env if env is not None else os.environ
        missing = [
            key
            for key in (
                "WATCHDOG_POLL_INTERVAL_SEC",
                "WATCHDOG_HEARTBEAT_INTERVAL_SEC",
                "WATCHDOG_HEARTBEAT_TIMEOUT_SEC",
            )
            if key not in source or not str(source[key]).strip()
        ]
        if missing:
            raise WatchdogConfigError(
                f"Missing required watchdog configuration env vars: {', '.join(missing)}"
            )
        try:
            poll = float(source["WATCHDOG_POLL_INTERVAL_SEC"])
            hb_interval = float(source["WATCHDOG_HEARTBEAT_INTERVAL_SEC"])
            hb_timeout = float(source["WATCHDOG_HEARTBEAT_TIMEOUT_SEC"])
        except (TypeError, ValueError) as exc:
            raise WatchdogConfigError(f"Invalid numeric watchdog env var: {exc}") from exc

        return cls(
            worker_id=worker_id,
            db_path=db_path,
            poll_interval_sec=poll,
            heartbeat_interval_sec=hb_interval,
            heartbeat_timeout_sec=hb_timeout,
        )


# ---------------------------------------------------------------------------
# Windows Named Mutex via ctypes
# ---------------------------------------------------------------------------


class MutexApiProtocol(Protocol):
    """Minimal Win32 Mutex API protocol for native execution and unit testing."""

    def create_mutex(self, name: str) -> Tuple[int, int]:
        """Create or open a named mutex. Returns (handle, last_error)."""
        ...

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        """Wait for the mutex handle. Returns WAIT_* constant."""
        ...

    def release_mutex(self, handle: int) -> bool:
        """Release ownership of the mutex handle."""
        ...

    def close_handle(self, handle: int) -> bool:
        """Close the Win32 handle."""
        ...

    def get_last_error(self) -> int:
        """Return last Win32 error code."""
        ...


class Win32MutexApi:
    """Native Windows Mutex API wrapper using ctypes kernel32."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise WatchdogMutexError("Win32MutexApi is only supported on Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE

        self._kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD

        self._kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self._kernel32.ReleaseMutex.restype = wintypes.BOOL

        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def create_mutex(self, name: str) -> Tuple[int, int]:
        ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, False, name)
        last_err = ctypes.get_last_error()
        return int(handle or 0), int(last_err)

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        ctypes.set_last_error(0)
        res = self._kernel32.WaitForSingleObject(wintypes.HANDLE(handle), wintypes.DWORD(timeout_ms))
        return int(res)

    def release_mutex(self, handle: int) -> bool:
        ctypes.set_last_error(0)
        return bool(self._kernel32.ReleaseMutex(wintypes.HANDLE(handle)))

    def close_handle(self, handle: int) -> bool:
        ctypes.set_last_error(0)
        return bool(self._kernel32.CloseHandle(wintypes.HANDLE(handle)))

    def get_last_error(self) -> int:
        return int(ctypes.get_last_error())


@dataclass(frozen=True)
class MutexAcquireResult:
    """Outcome of acquiring a Windows Named Mutex."""

    status: MutexAcquireStatus
    mutex_name: str
    already_existed: bool = False
    win32_error: int = 0
    message: str = ""

    @property
    def acquired(self) -> bool:
        return self.status in (MutexAcquireStatus.ACQUIRED, MutexAcquireStatus.ABANDONED)


class WindowsNamedMutex:
    """Thread-safe, idempotent Windows Named Mutex wrapper."""

    def __init__(self, name: str, api: Optional[MutexApiProtocol] = None) -> None:
        self._name = name
        self._api = api or Win32MutexApi()
        self._lock = threading.Lock()
        self._handle: Optional[int] = None
        self._owned: bool = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_owned(self) -> bool:
        with self._lock:
            return self._owned

    def acquire(self, timeout_ms: int = 0) -> MutexAcquireResult:
        """Attempt to acquire the named mutex without blocking indefinitely."""
        with self._lock:
            if self._owned and self._handle:
                return MutexAcquireResult(
                    status=MutexAcquireStatus.ACQUIRED,
                    mutex_name=self._name,
                    already_existed=True,
                    message="Mutex already owned by this instance",
                )

            handle, last_err = self._api.create_mutex(self._name)
            if not handle:
                return MutexAcquireResult(
                    status=MutexAcquireStatus.FAILED,
                    mutex_name=self._name,
                    win32_error=last_err,
                    message=f"CreateMutexW failed (error={last_err})",
                )

            already_existed = last_err == ERROR_ALREADY_EXISTS
            wait_result = self._api.wait_for_single_object(handle, timeout_ms)

            if wait_result == WAIT_OBJECT_0:
                self._handle = handle
                self._owned = True
                return MutexAcquireResult(
                    status=MutexAcquireStatus.ACQUIRED,
                    mutex_name=self._name,
                    already_existed=already_existed,
                    message="Mutex acquired (WAIT_OBJECT_0)",
                )

            if wait_result == WAIT_ABANDONED:
                self._handle = handle
                self._owned = True
                return MutexAcquireResult(
                    status=MutexAcquireStatus.ABANDONED,
                    mutex_name=self._name,
                    already_existed=True,
                    message="Mutex acquired after previous owner terminated abruptly (WAIT_ABANDONED)",
                )

            # Not owned: close handle immediately
            self._api.close_handle(handle)
            self._handle = None
            self._owned = False

            if wait_result == WAIT_TIMEOUT:
                return MutexAcquireResult(
                    status=MutexAcquireStatus.ALREADY_HELD,
                    mutex_name=self._name,
                    already_existed=True,
                    message="Mutex is already held by another instance (WAIT_TIMEOUT)",
                )

            err = self._api.get_last_error()
            return MutexAcquireResult(
                status=MutexAcquireStatus.FAILED,
                mutex_name=self._name,
                already_existed=already_existed,
                win32_error=err,
                message=f"WaitForSingleObject failed (result={wait_result}, error={err})",
            )

    def release(self) -> bool:
        """Release mutex ownership (only if owned) and close handle. Idempotent."""
        with self._lock:
            if self._handle is None:
                self._owned = False
                return True

            released_ok = True
            handle = self._handle
            was_owned = self._owned

            self._handle = None
            self._owned = False

            try:
                if was_owned:
                    if not self._api.release_mutex(handle):
                        released_ok = False
            finally:
                if not self._api.close_handle(handle):
                    released_ok = False

            return released_ok


# ---------------------------------------------------------------------------
# Monitoring & Health Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionObservation:
    """Read-only monitoring observation for a single persisted execution."""

    execution_id: str
    attempt_id: str
    fencing_token: int
    execution_status: ExecutionStatus
    monitoring_status: MonitoringStatus
    last_heartbeat_at_utc: Optional[str] = None
    heartbeat_age_sec: Optional[float] = None
    identity_count: int = 0
    reason: str = ""


@dataclass(frozen=True)
class MonitoringCycleResult:
    """Structured result of a single WatchdogDaemon monitoring cycle."""

    cycle_id: str
    timestamp_utc: str
    overall_status: MonitoringStatus
    executions_total: int = 0
    executions_by_status: Dict[str, int] = field(default_factory=dict)
    observations: List[ExecutionObservation] = field(default_factory=list)
    integrity_failures: int = 0
    outbox_available: Optional[bool] = None
    outbox_integrity_failures: int = 0
    abandoned_mutex_recovery: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class HealthCheckSnapshot:
    """Health snapshot of the WatchdogDaemon itself (NOT proof of upload health)."""

    worker_id: str
    daemon_instance_id: str
    active: bool
    mutex_acquired: bool
    mutex_name: str
    state_store_accessible: bool
    last_cycle_id: Optional[str]
    last_cycle_at_utc: Optional[str]
    last_cycle_status: Optional[MonitoringStatus]
    current_error: Optional[str]
    ipc_running: bool = False
    ipc_port: Optional[int] = None


# ---------------------------------------------------------------------------
# WatchdogDaemon
# ---------------------------------------------------------------------------


_STATUS_SEVERITY: Dict[MonitoringStatus, int] = {
    MonitoringStatus.HEALTHY: 0,
    MonitoringStatus.HEARTBEAT_EXPIRED: 1,
    MonitoringStatus.INCONCLUSIVE: 2,
    MonitoringStatus.STORE_UNAVAILABLE: 3,
}


class WatchdogDaemon:
    """Independent Local Watchdog Daemon process controller.

    Responsibilities:
    - Acquire and hold the Windows Named Mutex (`Local\\DCF_WATCHDOG_LOCK_<worker_id>`).
    - Open and validate the local SQLite State Store (Task 3).
    - Periodically monitor persisted executions and heartbeats without mutating
      execution status or terminating any external processes.
    - Handle WAIT_ABANDONED with mandatory State Store integrity verification.
    - Shut down cleanly, releasing handles and Mutex in `finally`.
    """

    def __init__(
        self,
        config: WatchdogConfig,
        *,
        mutex_api: Optional[MutexApiProtocol] = None,
        store_factory: Optional[Callable[[Union[str, Path]], StateStore]] = None,
        clock: Optional[Callable[[], datetime]] = None,
        daemon_instance_id: Optional[str] = None,
        termination_engine: Optional[Any] = None,
        ipc_host: str = "127.0.0.1",
        ipc_port: int = 0,
        ipc_auth_token: Optional[str] = None,
        expected_launcher_pid: Optional[int] = None,
        expected_launcher_create_time: Optional[int] = None,
        ipc_server_factory: Optional[Callable[..., JobHandleTransferServer]] = None,
        ipc_enabled: Optional[bool] = None,
    ) -> None:
        if not isinstance(config, WatchdogConfig):
            raise WatchdogConfigError("config must be a WatchdogConfig instance")

        self.config = config
        self.daemon_instance_id = daemon_instance_id or f"wd-{uuid.uuid4().hex[:12]}"
        self._mutex = WindowsNamedMutex(config.mutex_name, api=mutex_api)
        self._store_factory = store_factory or (lambda path: StateStore(path))
        self._clock = clock or utc_now
        self._termination_engine = termination_engine

        self._ipc_host = ipc_host
        self._ipc_port = int(ipc_port)
        self._ipc_auth_token = ipc_auth_token
        self._expected_launcher_pid = expected_launcher_pid
        self._expected_launcher_create_time = expected_launcher_create_time
        self._ipc_server_factory = ipc_server_factory
        if ipc_enabled is not None:
            self._ipc_enabled: bool = bool(ipc_enabled)
        else:
            self._ipc_enabled = any(
                x is not None
                for x in (
                    termination_engine,
                    ipc_auth_token,
                    expected_launcher_pid,
                    expected_launcher_create_time,
                    ipc_server_factory,
                )
            ) or (self._ipc_port != 0)
        self._ipc_server: Optional[JobHandleTransferServer] = None
        self._ipc_started: bool = False
        self._ipc_error: Optional[str] = None

        self._store: Optional[StateStore] = None
        self._state_lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._stop_event = threading.Event()

        self._running: bool = False
        self._state_store_accessible: bool = False
        self._abandoned_mutex_detected: bool = False
        self._last_cycle_result: Optional[MonitoringCycleResult] = None
        self._current_error: Optional[str] = None

    # -----------------------------------------------------------------------
    # Structured Logging
    # -----------------------------------------------------------------------

    def _log(
        self,
        level: int,
        message: str,
        *,
        cycle_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        fencing_token: Optional[int] = None,
        monitoring_status: Optional[MonitoringStatus] = None,
        error_code: Optional[str] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "worker_id": self.config.worker_id,
            "daemon_instance_id": self.daemon_instance_id,
            "timestamp_utc": format_utc_iso(self._clock()),
            "cycle_id": cycle_id,
            "execution_id": execution_id,
            "attempt_id": attempt_id,
            "fencing_token": fencing_token,
            "monitoring_status": monitoring_status.value if monitoring_status else None,
            "error_code": error_code,
        }
        logger.log(level, message, extra=payload)

    # -----------------------------------------------------------------------
    # Mutex Management
    # -----------------------------------------------------------------------

    def acquire_mutex(self) -> MutexAcquireResult:
        """Acquire the Windows Named Mutex for this worker_id."""
        result = self._mutex.acquire(timeout_ms=0)
        if result.status == MutexAcquireStatus.ACQUIRED:
            self._log(
                logging.INFO,
                f"Acquired Windows Named Mutex {result.mutex_name}",
            )
        elif result.status == MutexAcquireStatus.ABANDONED:
            self._abandoned_mutex_detected = True
            self._log(
                logging.WARNING,
                f"Acquired ABANDONED Windows Named Mutex {result.mutex_name}; "
                "State Store validation required before operating",
                monitoring_status=MonitoringStatus.INCONCLUSIVE,
                error_code="MUTEX_ABANDONED",
            )
        elif result.status == MutexAcquireStatus.ALREADY_HELD:
            self._log(
                logging.WARNING,
                f"Another WatchdogDaemon instance already holds {result.mutex_name}",
                error_code="MUTEX_ALREADY_HELD",
            )
        else:
            self._log(
                logging.ERROR,
                f"Failed to acquire Windows Named Mutex {result.mutex_name}: {result.message}",
                error_code="MUTEX_ACQUIRE_FAILED",
            )
        return result

    def release_mutex(self) -> bool:
        """Release the Windows Named Mutex (idempotent)."""
        released = self._mutex.release()
        if not released:
            self._log(
                logging.ERROR,
                f"Failed to cleanly release Windows Named Mutex {self._mutex.name}",
                error_code="MUTEX_RELEASE_FAILED",
            )
        return released

    # -----------------------------------------------------------------------
    # Lifecycle & Health
    # -----------------------------------------------------------------------

    def is_running(self) -> bool:
        """Return True if the daemon is currently active and not stopping."""
        with self._state_lock:
            return self._running and not self._stop_event.is_set()

    @property
    def ipc_running(self) -> bool:
        with self._state_lock:
            return (
                self._ipc_started
                and self._ipc_server is not None
                and self._ipc_server.is_running
            )

    @property
    def ipc_address(self) -> Optional[Tuple[str, int]]:
        with self._state_lock:
            if not self._ipc_started or self._ipc_server is None:
                return None
            return (self._ipc_host, self._ipc_server.port)

    @property
    def ipc_port(self) -> Optional[int]:
        addr = self.ipc_address
        return addr[1] if addr is not None else None

    @property
    def ipc_auth_token(self) -> Optional[str]:
        with self._state_lock:
            if self._ipc_server is not None:
                return self._ipc_server.auth_token
            return self._ipc_auth_token

    @property
    def ipc_error(self) -> Optional[str]:
        with self._state_lock:
            return self._ipc_error

    def _on_ipc_register_handle(
        self,
        execution_id: str,
        job_handle: int,
        attempt_id: str,
        fencing_token: int,
        source_pid: int,
        source_process_create_time: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Real IPC callback bound to SafeTerminationEngine.accept_transferred_job_handle."""
        if self._stop_event.is_set() or not self._mutex.is_owned:
            return False, "WatchdogDaemon is not active or does not own Mutex"
        if self._termination_engine is None:
            return False, "No termination_engine configured on WatchdogDaemon"
        wd_ct = (
            self._ipc_server.watchdog_create_time
            if self._ipc_server is not None
            else None
        )
        return self._termination_engine.accept_transferred_job_handle(
            execution_id,
            job_handle,
            attempt_id=attempt_id,
            fencing_token=fencing_token,
            source_pid=source_pid,
            source_process_create_time=source_process_create_time,
            watchdog_pid=os.getpid(),
            watchdog_process_create_time=wd_ct,
            daemon_instance_id=self.daemon_instance_id,
        )

    def _on_ipc_unregister_handle(self, execution_id: str) -> bool:
        """Real IPC callback bound to SafeTerminationEngine.unregister_job_handle."""
        if self._termination_engine is None:
            return False
        return bool(self._termination_engine.unregister_job_handle(execution_id))

    def reauthorize_launcher(
        self,
        *,
        new_launcher_pid: int,
        new_launcher_create_time: int,
        auth_token: str,
        reason: str = "launcher_restart",
    ) -> Tuple[int, int]:
        """Explicitly reauthorize a restarted Launcher process on the active IPC server."""
        if self._stop_event.is_set() or not self._mutex.is_owned:
            raise WatchdogDaemonError(
                "Cannot reauthorize launcher: WatchdogDaemon is not active or does not own Mutex"
            )
        with self._state_lock:
            server = self._ipc_server
        if server is None or not server.is_running:
            raise WatchdogIPCError(
                "Cannot reauthorize launcher: IPC handle transfer server is not running"
            )
        result = server.reauthorize_launcher(
            new_launcher_pid=new_launcher_pid,
            new_launcher_create_time=new_launcher_create_time,
            auth_token=auth_token,
            reason=reason,
        )
        self._expected_launcher_pid = result[0]
        self._expected_launcher_create_time = result[1]
        return result

    def _start_ipc_server(self) -> None:
        """Start the authenticated JobHandleTransferServer during daemon startup."""
        if not self._ipc_enabled:
            return
        factory = self._ipc_server_factory or JobHandleTransferServer
        try:
            server = factory(
                on_register=self._on_ipc_register_handle,
                on_unregister=self._on_ipc_unregister_handle,
                auth_token=self._ipc_auth_token,
                host=self._ipc_host,
                port=self._ipc_port,
                daemon_instance_id=self.daemon_instance_id,
                expected_launcher_pid=self._expected_launcher_pid,
                expected_launcher_create_time=self._expected_launcher_create_time,
            )
            host, port = server.start()
            with self._state_lock:
                self._ipc_server = server
                self._ipc_started = True
                self._ipc_error = None
            self._log(
                logging.INFO,
                f"Started JobHandleTransferServer on {host}:{port}",
            )
        except Exception as exc:
            err_msg = f"Failed to start JobHandleTransferServer: {exc}"
            with self._state_lock:
                self._ipc_server = None
                self._ipc_started = False
                self._ipc_error = err_msg
                self._current_error = err_msg
            self._log(
                logging.ERROR,
                err_msg,
                error_code="IPC_SERVER_START_FAILED",
            )
            raise WatchdogIPCError(err_msg) from exc

    def _stop_ipc_server(self, timeout_sec: float = 2.0) -> bool:
        """Deterministically close the JobHandleTransferServer and verify its thread exits.

        Returns ``True`` iff the IPC server thread exited cleanly (or was not running).
        Returns ``False`` if the thread failed to exit within ``timeout_sec``.
        """
        with self._state_lock:
            server = self._ipc_server
        if server is None:
            with self._state_lock:
                self._ipc_started = False
            return True
        try:
            closed = server.close(timeout_sec=timeout_sec)
            # Support custom/mock servers whose close() returns None
            if closed is False:
                self._log(
                    logging.ERROR,
                    "JobHandleTransferServer thread did not exit within timeout; "
                    "refusing to declare clean shutdown",
                    error_code="IPC_SERVER_STOP_TIMEOUT",
                )
                return False
            with self._state_lock:
                self._ipc_server = None
                self._ipc_started = False
            self._log(logging.INFO, "Stopped JobHandleTransferServer cleanly")
            return True
        except Exception as exc:
            self._log(
                logging.ERROR,
                f"Error stopping JobHandleTransferServer: {exc}",
                error_code="IPC_SERVER_STOP_ERROR",
            )
            return False

    def request_safe_termination(
        self,
        context: Any,
        *,
        timeout_ms: Optional[int] = None,
        exit_code: int = 1,
    ) -> Any:
        """Delegate a safe termination request to the configured termination engine.

        Fail-closed: requires configured ``termination_engine``, running IPC
        handle transfer server, and Mutex ownership. Acquires ``_cycle_lock``
        so daemon shutdown waits for any in-flight termination to finish cleanly.
        """
        if self._termination_engine is None:
            raise WatchdogDaemonError(
                "No termination_engine configured on WatchdogDaemon"
            )
        if not self._mutex.is_owned:
            raise WatchdogDaemonError(
                "Cannot execute safe termination without Mutex ownership"
            )
        if not self.ipc_running:
            raise WatchdogIPCError(
                "Cannot execute safe termination: IPC handle transfer server is not running"
            )
        with self._cycle_lock:
            return self._termination_engine.terminate_execution(
                context,
                timeout_ms=timeout_ms,
                exit_code=exit_code,
            )

    def health_check(self) -> HealthCheckSnapshot:
        """Return current daemon health snapshot (does not imply upload health)."""
        with self._state_lock:
            last = self._last_cycle_result
            ipc_up = (
                self._ipc_started
                and self._ipc_server is not None
                and self._ipc_server.is_running
            )
            ipc_port = self._ipc_server.port if (ipc_up and self._ipc_server is not None) else None
            return HealthCheckSnapshot(
                worker_id=self.config.worker_id,
                daemon_instance_id=self.daemon_instance_id,
                active=self._running and not self._stop_event.is_set(),
                mutex_acquired=self._mutex.is_owned,
                mutex_name=self._mutex.name,
                state_store_accessible=self._state_store_accessible,
                last_cycle_id=last.cycle_id if last else None,
                last_cycle_at_utc=last.timestamp_utc if last else None,
                last_cycle_status=last.overall_status if last else None,
                current_error=self._current_error,
                ipc_running=ipc_up,
                ipc_port=ipc_port,
            )

    _REQUIRED_STATE_STORE_TABLES = frozenset(
        {
            "schema_metadata",
            "upload_execution",
            "upload_attempt",
            "process_identity",
            "termination_evidence",
            "watchdog_checkpoint",
        }
    )

    def _validate_state_store_path(self) -> Optional[str]:
        """Validate that db_path is an existing regular SQLite file with compatible schema.

        Strictly read-only: never creates files, directories, tables, or migrations.
        Returns None if valid, or an error message string if invalid.
        """
        db_path = Path(self.config.db_path)
        if not db_path.exists():
            return f"State Store path does not exist: {db_path}"
        if not db_path.is_file():
            return f"State Store path is not a regular file: {db_path}"

        uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
        ro_conn: Optional[sqlite3.Connection] = None
        try:
            ro_conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            ro_conn.row_factory = sqlite3.Row
            tables = {
                str(r["name"])
                for r in ro_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table';"
                ).fetchall()
            }
            missing_tables = sorted(self._REQUIRED_STATE_STORE_TABLES - tables)
            if missing_tables:
                return (
                    f"State Store at {db_path} has incompatible schema "
                    f"(missing tables: {', '.join(missing_tables)})"
                )
            ver_row = ro_conn.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version';"
            ).fetchone()
            if ver_row is None:
                return f"State Store at {db_path} missing schema_version in schema_metadata"
            try:
                version = int(ver_row["value"])
            except (TypeError, ValueError):
                return f"State Store at {db_path} has non-integer schema_version: {ver_row['value']!r}"
            if version != STATE_STORE_SCHEMA_VERSION:
                return (
                    f"State Store at {db_path} has incompatible schema_version "
                    f"{version} (expected {STATE_STORE_SCHEMA_VERSION})"
                )
            return None
        except (sqlite3.Error, OSError) as exc:
            return f"State Store read-only validation failed for {db_path}: {exc}"
        finally:
            if ro_conn is not None:
                try:
                    ro_conn.close()
                except Exception:
                    pass

    def _open_store(self) -> Optional[StateStore]:
        """Attempt to open an existing, validated local State Store without creating or migrating."""
        if self._store is not None:
            return self._store

        validation_error = self._validate_state_store_path()
        if validation_error is not None:
            with self._state_lock:
                self._state_store_accessible = False
                self._current_error = validation_error
            self._log(
                logging.ERROR,
                validation_error,
                monitoring_status=MonitoringStatus.STORE_UNAVAILABLE,
                error_code="STATE_STORE_PATH_INVALID",
            )
            return None

        try:
            store = self._store_factory(self.config.db_path)
            # Validate pragmas / basic accessibility
            store.get_pragmas()
            self._store = store
            with self._state_lock:
                self._state_store_accessible = True
            return store
        except (StateStoreError, sqlite3.Error, OSError, Exception) as exc:
            with self._state_lock:
                self._state_store_accessible = False
                self._current_error = f"StateStore open failed: {exc}"
            self._log(
                logging.ERROR,
                f"Failed to open State Store at {self.config.db_path}: {exc}",
                monitoring_status=MonitoringStatus.STORE_UNAVAILABLE,
                error_code="STATE_STORE_OPEN_FAILED",
            )
            return None

    def _close_store(self) -> None:
        if self._store is not None:
            try:
                self._store.close()
            except Exception as exc:
                self._log(
                    logging.WARNING,
                    f"Error closing State Store: {exc}",
                    error_code="STATE_STORE_CLOSE_ERROR",
                )
            finally:
                self._store = None
                with self._state_lock:
                    self._state_store_accessible = False

    def start(self) -> MutexAcquireResult:
        """Acquire Mutex, open State Store, and mark daemon as running.

        Raises:
            WatchdogAlreadyRunningError: if another instance holds the Mutex.
            WatchdogMutexError: if Mutex acquisition fails.
        """
        self._stop_event.clear()
        mutex_result = self.acquire_mutex()

        if mutex_result.status == MutexAcquireStatus.ALREADY_HELD:
            raise WatchdogAlreadyRunningError(mutex_result.message)
        if not mutex_result.acquired:
            raise WatchdogMutexError(mutex_result.message)

        try:
            self._open_store()
            self._start_ipc_server()
            with self._state_lock:
                self._running = True
            self._log(
                logging.INFO,
                "WatchdogDaemon started",
                monitoring_status=(
                    MonitoringStatus.INCONCLUSIVE
                    if self._abandoned_mutex_detected
                    else (
                        MonitoringStatus.HEALTHY
                        if self._state_store_accessible
                        else MonitoringStatus.STORE_UNAVAILABLE
                    )
                ),
            )
            return mutex_result
        except Exception:
            self._stop_ipc_server()
            self._close_store()
            self.release_mutex()
            raise

    def stop(self, timeout_sec: Optional[float] = None) -> bool:
        """Request controlled shutdown, stop IPC server, wait for active cycle, and release resources.

        If the IPC server thread or an active monitoring/termination cycle does
        not complete within `timeout_sec`, this method refuses to close
        shared resources (`termination_engine`, `StateStore`) or release the
        Named Mutex so no second instance can acquire the Mutex while the IPC
        server or active cycle may still be processing requests.
        """
        wait_timeout = (
            self.config.stop_timeout_sec if timeout_sec is None else float(timeout_sec)
        )
        self._stop_event.set()
        with self._state_lock:
            self._running = False

        ipc_stopped = self._stop_ipc_server(timeout_sec=wait_timeout)
        if not ipc_stopped:
            self._log(
                logging.ERROR,
                "WatchdogDaemon did not complete clean shutdown: IPC server thread did not "
                f"exit within {wait_timeout:.2f}s; State Store kept open and Mutex held",
                error_code="UNCLEAN_SHUTDOWN",
            )
            return False

        clean_cycle_finish = self._cycle_lock.acquire(timeout=max(0.0, wait_timeout))
        if not clean_cycle_finish:
            self._log(
                logging.ERROR,
                "WatchdogDaemon did not complete clean shutdown: active cycle did not "
                f"finish within {wait_timeout:.2f}s; State Store kept open and Mutex held",
                error_code="UNCLEAN_SHUTDOWN",
            )
            return False

        try:
            if self._termination_engine is not None and hasattr(
                self._termination_engine, "close"
            ):
                try:
                    self._termination_engine.close()
                except Exception as exc:
                    self._log(
                        logging.WARNING,
                        f"Error closing termination_engine handles: {exc}",
                        error_code="TERMINATION_ENGINE_CLOSE_ERROR",
                    )
            self._close_store()
            mutex_released = self.release_mutex()
        finally:
            self._cycle_lock.release()

        if mutex_released:
            self._log(logging.INFO, "WatchdogDaemon stopped cleanly")
            return True

        self._log(
            logging.ERROR,
            "WatchdogDaemon did not complete clean shutdown "
            "(cycle_finished=True, mutex_released=False)",
            error_code="UNCLEAN_SHUTDOWN",
        )
        return False

    # -----------------------------------------------------------------------
    # Monitoring Cycle
    # -----------------------------------------------------------------------

    def _check_outbox_readonly(self, store: StateStore) -> Tuple[Optional[bool], int]:
        """Strictly observational Outbox inspection using read-only SQLite queries.

        Never instantiates OutboxStore, never executes migrations, never creates
        tables/indexes, and never modifies schema_metadata or outbox rows.
        Any outdated schema (e.g. v1), missing column/index, or invalid checksum
        returns a non-zero failure count so the cycle is classified INCONCLUSIVE.
        """
        if not self.config.check_outbox:
            return None, 0
        try:
            with store._lock:
                conn = store._require_conn()
                table_row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='outbox_event';"
                ).fetchone()
                if table_row is None:
                    return False, 0

                # 1. Verify recorded outbox_schema_version without migrating
                ver_row = conn.execute(
                    "SELECT value FROM schema_metadata WHERE key = ?;",
                    (_OUTBOX_SCHEMA_META_KEY,),
                ).fetchone()
                if ver_row is None:
                    self._log(
                        logging.WARNING,
                        "Outbox table exists without outbox_schema_version metadata; "
                        "classified as INCONCLUSIVE (read-only check, no migration performed)",
                        monitoring_status=MonitoringStatus.INCONCLUSIVE,
                        error_code="OUTBOX_SCHEMA_INCOMPATIBLE",
                    )
                    return True, 1

                try:
                    recorded_version = int(ver_row["value"])
                except (TypeError, ValueError):
                    self._log(
                        logging.WARNING,
                        f"Outbox schema version is non-integer ({ver_row['value']!r}); "
                        "classified as INCONCLUSIVE",
                        monitoring_status=MonitoringStatus.INCONCLUSIVE,
                        error_code="OUTBOX_SCHEMA_INCOMPATIBLE",
                    )
                    return True, 1

                if recorded_version != OUTBOX_SCHEMA_VERSION:
                    self._log(
                        logging.WARNING,
                        f"Outbox schema version is v{recorded_version} (expected v{OUTBOX_SCHEMA_VERSION}); "
                        "classified as INCONCLUSIVE without migrating",
                        monitoring_status=MonitoringStatus.INCONCLUSIVE,
                        error_code="OUTBOX_SCHEMA_INCOMPATIBLE",
                    )
                    return True, 1

                # 2. Verify required v2 columns (read-only PRAGMA)
                existing_cols = {
                    str(r["name"])
                    for r in conn.execute("PRAGMA table_info(outbox_event);").fetchall()
                }
                missing_cols = sorted(_OUTBOX_V2_REQUIRED_COLUMNS - existing_cols)
                if missing_cols:
                    self._log(
                        logging.WARNING,
                        f"Outbox v{OUTBOX_SCHEMA_VERSION} missing columns: {', '.join(missing_cols)}; "
                        "classified as INCONCLUSIVE without altering schema",
                        monitoring_status=MonitoringStatus.INCONCLUSIVE,
                        error_code="OUTBOX_SCHEMA_INCOMPATIBLE",
                    )
                    return True, 1

                # 3. Verify required indexes structurally (read-only PRAGMA)
                existing_indexes = {
                    str(r["name"]): r
                    for r in conn.execute("PRAGMA index_list('outbox_event');").fetchall()
                }
                for spec in _OUTBOX_REQUIRED_INDEX_SPECS:
                    idx_row = existing_indexes.get(spec.name)
                    if idx_row is None:
                        self._log(
                            logging.WARNING,
                            f"Outbox missing required index '{spec.name}'; "
                            "classified as INCONCLUSIVE without recreating index",
                            monitoring_status=MonitoringStatus.INCONCLUSIVE,
                            error_code="OUTBOX_INDEX_INVALID",
                        )
                        return True, 1
                    idx_cols_rows = conn.execute(
                        f"PRAGMA index_info('{spec.name}');"
                    ).fetchall()
                    idx_cols = tuple(
                        r["name"] for r in sorted(idx_cols_rows, key=lambda r: int(r["seqno"]))
                    )
                    if (
                        idx_cols != spec.columns
                        or bool(idx_row["unique"]) != spec.unique
                        or bool(idx_row["partial"]) != spec.partial
                    ):
                        self._log(
                            logging.WARNING,
                            f"Outbox index '{spec.name}' definition mismatch; "
                            "classified as INCONCLUSIVE without recreating index",
                            monitoring_status=MonitoringStatus.INCONCLUSIVE,
                            error_code="OUTBOX_INDEX_INVALID",
                        )
                        return True, 1

                # 4. Verify row structural integrity and SHA-256 checksums (read-only SELECT)
                rows = conn.execute(
                    "SELECT * FROM outbox_event ORDER BY created_at_utc ASC, event_id ASC;"
                ).fetchall()

            failures = 0
            for row in rows:
                try:
                    row_ver = int(row["schema_version"])
                    if row_ver != OUTBOX_SCHEMA_VERSION:
                        failures += 1
                        continue
                    status_enum = OutboxEventStatus(str(row["status"]))
                    parsed_payload = json.loads(str(row["payload_json"]))
                    if not isinstance(parsed_payload, dict):
                        failures += 1
                        continue
                    fencing_raw = row["fencing_token"]
                    rec = OutboxEventRecord(
                        event_id=str(row["event_id"]),
                        event_type=str(row["event_type"]),
                        execution_id=row["execution_id"],
                        attempt_id=row["attempt_id"],
                        job_id=row["job_id"],
                        worker_id=str(row["worker_id"]),
                        fencing_token=int(fencing_raw) if fencing_raw is not None else None,
                        payload_json=str(row["payload_json"]),
                        created_at_utc=str(row["created_at_utc"]),
                        available_at_utc=str(row["available_at_utc"]),
                        claim_count=int(row["claim_count"]),
                        attempt_count=int(row["attempt_count"]),
                        last_attempt_at_utc=row["last_attempt_at_utc"],
                        delivered_at_utc=row["delivered_at_utc"],
                        last_error=row["last_error"],
                        status=status_enum,
                        lease_id=row["lease_id"],
                        lease_owner=row["lease_owner"],
                        lease_expires_at_utc=row["lease_expires_at_utc"],
                        ack_id=row["ack_id"],
                        schema_version=row_ver,
                        checksum_sha256=str(row["checksum_sha256"]),
                    )
                    expected_checksum = compute_canonical_checksum(_outbox_checksum_payload(rec))
                    if expected_checksum != rec.checksum_sha256:
                        failures += 1
                except Exception:
                    failures += 1

            return True, failures
        except (StateStoreError, sqlite3.Error) as exc:
            self._log(
                logging.WARNING,
                f"Outbox read-only integrity check failed: {exc}",
                monitoring_status=MonitoringStatus.INCONCLUSIVE,
                error_code="OUTBOX_CHECK_FAILED",
            )
            return False, 1

    def _evaluate_execution(
        self,
        store: StateStore,
        execution: ExecutionRecord,
        now_dt: datetime,
        cycle_id: str,
    ) -> ExecutionObservation:
        """Evaluate a single persisted execution without mutating State Store."""
        if execution.status == ExecutionStatus.INCONCLUSIVE:
            return ExecutionObservation(
                execution_id=execution.execution_id,
                attempt_id=execution.attempt_id,
                fencing_token=execution.fencing_token,
                execution_status=execution.status,
                monitoring_status=MonitoringStatus.INCONCLUSIVE,
                reason="Execution already classified as INCONCLUSIVE",
            )

        identities: List[ProcessIdentityRecord] = store.get_process_identities(
            execution.execution_id
        )
        attempt: Optional[AttemptRecord] = store.get_attempt(execution.attempt_id)

        if execution.status == ExecutionStatus.CLOSED:
            return ExecutionObservation(
                execution_id=execution.execution_id,
                attempt_id=execution.attempt_id,
                fencing_token=execution.fencing_token,
                execution_status=execution.status,
                monitoring_status=MonitoringStatus.HEALTHY,
                last_heartbeat_at_utc=attempt.last_heartbeat_at_utc if attempt else None,
                identity_count=len(identities),
                reason="Execution is CLOSED",
            )

        # Active execution (RUNNING or TERMINATING)
        if not identities:
            return ExecutionObservation(
                execution_id=execution.execution_id,
                attempt_id=execution.attempt_id,
                fencing_token=execution.fencing_token,
                execution_status=execution.status,
                monitoring_status=MonitoringStatus.INCONCLUSIVE,
                last_heartbeat_at_utc=attempt.last_heartbeat_at_utc if attempt else None,
                identity_count=0,
                reason="Active execution missing persisted process_identity",
            )

        if attempt is None or not attempt.last_heartbeat_at_utc:
            return ExecutionObservation(
                execution_id=execution.execution_id,
                attempt_id=execution.attempt_id,
                fencing_token=execution.fencing_token,
                execution_status=execution.status,
                monitoring_status=MonitoringStatus.INCONCLUSIVE,
                last_heartbeat_at_utc=None,
                identity_count=len(identities),
                reason="Active execution missing attempt or last_heartbeat_at_utc",
            )

        try:
            hb_dt = parse_utc_iso(attempt.last_heartbeat_at_utc)
        except ValueError as exc:
            return ExecutionObservation(
                execution_id=execution.execution_id,
                attempt_id=execution.attempt_id,
                fencing_token=execution.fencing_token,
                execution_status=execution.status,
                monitoring_status=MonitoringStatus.INCONCLUSIVE,
                last_heartbeat_at_utc=attempt.last_heartbeat_at_utc,
                identity_count=len(identities),
                reason=f"Invalid last_heartbeat_at_utc format: {exc}",
            )

        age_sec = (now_dt - hb_dt).total_seconds()
        if age_sec > self.config.heartbeat_timeout_sec:
            return ExecutionObservation(
                execution_id=execution.execution_id,
                attempt_id=execution.attempt_id,
                fencing_token=execution.fencing_token,
                execution_status=execution.status,
                monitoring_status=MonitoringStatus.HEARTBEAT_EXPIRED,
                last_heartbeat_at_utc=attempt.last_heartbeat_at_utc,
                heartbeat_age_sec=age_sec,
                identity_count=len(identities),
                reason=(
                    f"Heartbeat age {age_sec:.2f}s exceeded timeout "
                    f"{self.config.heartbeat_timeout_sec:.2f}s (requires investigation; "
                    "does NOT prove process termination)"
                ),
            )

        return ExecutionObservation(
            execution_id=execution.execution_id,
            attempt_id=execution.attempt_id,
            fencing_token=execution.fencing_token,
            execution_status=execution.status,
            monitoring_status=MonitoringStatus.HEALTHY,
            last_heartbeat_at_utc=attempt.last_heartbeat_at_utc,
            heartbeat_age_sec=age_sec,
            identity_count=len(identities),
            reason="Heartbeat within configured timeout",
        )

    def run_once(self) -> MonitoringCycleResult:
        """Execute a single read-only monitoring cycle against the State Store."""
        with self._cycle_lock:
            cycle_id = f"cyc-{uuid.uuid4().hex[:12]}"
            now_dt = self._clock()
            now_iso = format_utc_iso(now_dt)
            abandoned_recovery = self._abandoned_mutex_detected

            if not self._mutex.is_owned:
                result = MonitoringCycleResult(
                    cycle_id=cycle_id,
                    timestamp_utc=now_iso,
                    overall_status=MonitoringStatus.STORE_UNAVAILABLE,
                    abandoned_mutex_recovery=abandoned_recovery,
                    error_code="MUTEX_NOT_OWNED",
                    error_message="Cannot execute monitoring cycle without Mutex ownership",
                )
                self._record_cycle_result(result, error=result.error_message)
                return result

            store = self._open_store()
            if store is None:
                result = MonitoringCycleResult(
                    cycle_id=cycle_id,
                    timestamp_utc=now_iso,
                    overall_status=MonitoringStatus.STORE_UNAVAILABLE,
                    abandoned_mutex_recovery=abandoned_recovery,
                    error_code="STATE_STORE_UNAVAILABLE",
                    error_message=self._current_error or "State Store is unavailable",
                )
                self._record_cycle_result(result, error=result.error_message)
                return result

            try:
                integrity_reports = store.verify_integrity()
                integrity_failures = sum(1 for r in integrity_reports if not r.is_valid)

                outbox_available, outbox_failures = self._check_outbox_readonly(store)

                if integrity_failures > 0:
                    result = MonitoringCycleResult(
                        cycle_id=cycle_id,
                        timestamp_utc=now_iso,
                        overall_status=MonitoringStatus.INCONCLUSIVE,
                        integrity_failures=integrity_failures,
                        outbox_available=outbox_available,
                        outbox_integrity_failures=outbox_failures,
                        abandoned_mutex_recovery=abandoned_recovery,
                        error_code="STATE_STORE_INTEGRITY_FAILURE",
                        error_message=(
                            f"Detected {integrity_failures} corrupted/inconsistent record(s) "
                            "in State Store; classified as INCONCLUSIVE"
                        ),
                    )
                    self._log(
                        logging.ERROR,
                        result.error_message or "State Store integrity failure",
                        cycle_id=cycle_id,
                        monitoring_status=MonitoringStatus.INCONCLUSIVE,
                        error_code=result.error_code,
                    )
                    self._record_cycle_result(result, error=result.error_message)
                    return result

                executions = store.list_executions_by_status(
                    [
                        ExecutionStatus.RUNNING,
                        ExecutionStatus.TERMINATING,
                        ExecutionStatus.CLOSED,
                        ExecutionStatus.INCONCLUSIVE,
                    ]
                )

                counts: Dict[str, int] = {s.value: 0 for s in ExecutionStatus}
                observations: List[ExecutionObservation] = []
                overall = MonitoringStatus.HEALTHY

                if outbox_failures > 0:
                    overall = MonitoringStatus.INCONCLUSIVE

                for exc_rec in executions:
                    counts[exc_rec.status.value] = counts.get(exc_rec.status.value, 0) + 1
                    obs = self._evaluate_execution(store, exc_rec, now_dt, cycle_id)
                    observations.append(obs)
                    if _STATUS_SEVERITY[obs.monitoring_status] > _STATUS_SEVERITY[overall]:
                        overall = obs.monitoring_status

                    log_level = (
                        logging.WARNING
                        if obs.monitoring_status != MonitoringStatus.HEALTHY
                        else logging.DEBUG
                    )
                    self._log(
                        log_level,
                        f"Monitored execution {obs.execution_id}: {obs.monitoring_status.value}",
                        cycle_id=cycle_id,
                        execution_id=obs.execution_id,
                        attempt_id=obs.attempt_id,
                        fencing_token=obs.fencing_token,
                        monitoring_status=obs.monitoring_status,
                    )

                if abandoned_recovery:
                    self._abandoned_mutex_detected = False

                result = MonitoringCycleResult(
                    cycle_id=cycle_id,
                    timestamp_utc=now_iso,
                    overall_status=overall,
                    executions_total=len(executions),
                    executions_by_status=counts,
                    observations=observations,
                    integrity_failures=0,
                    outbox_available=outbox_available,
                    outbox_integrity_failures=outbox_failures,
                    abandoned_mutex_recovery=abandoned_recovery,
                )
                self._log(
                    logging.INFO,
                    f"Completed monitoring cycle {cycle_id}: {overall.value} "
                    f"(executions={len(executions)})",
                    cycle_id=cycle_id,
                    monitoring_status=overall,
                )
                self._record_cycle_result(result, error=None)
                return result

            except StateStoreCorruptionError as exc:
                result = MonitoringCycleResult(
                    cycle_id=cycle_id,
                    timestamp_utc=now_iso,
                    overall_status=MonitoringStatus.INCONCLUSIVE,
                    integrity_failures=1,
                    abandoned_mutex_recovery=abandoned_recovery,
                    error_code="STATE_STORE_CORRUPTION",
                    error_message=str(exc),
                )
                self._log(
                    logging.ERROR,
                    f"State Store corruption during cycle {cycle_id}: {exc}",
                    cycle_id=cycle_id,
                    monitoring_status=MonitoringStatus.INCONCLUSIVE,
                    error_code=result.error_code,
                )
                self._record_cycle_result(result, error=str(exc))
                return result

            except (StateStoreSchemaError, StateStorePersistenceError, StateStoreError, sqlite3.Error) as exc:
                self._close_store()
                result = MonitoringCycleResult(
                    cycle_id=cycle_id,
                    timestamp_utc=now_iso,
                    overall_status=MonitoringStatus.STORE_UNAVAILABLE,
                    abandoned_mutex_recovery=abandoned_recovery,
                    error_code="STATE_STORE_ERROR",
                    error_message=str(exc),
                )
                self._log(
                    logging.ERROR,
                    f"State Store unavailable during cycle {cycle_id}: {exc}",
                    cycle_id=cycle_id,
                    monitoring_status=MonitoringStatus.STORE_UNAVAILABLE,
                    error_code=result.error_code,
                )
                self._record_cycle_result(result, error=str(exc))
                return result

            except Exception as exc:
                result = MonitoringCycleResult(
                    cycle_id=cycle_id,
                    timestamp_utc=now_iso,
                    overall_status=MonitoringStatus.INCONCLUSIVE,
                    abandoned_mutex_recovery=abandoned_recovery,
                    error_code="UNEXPECTED_CYCLE_ERROR",
                    error_message=str(exc),
                )
                self._log(
                    logging.ERROR,
                    f"Unexpected exception during cycle {cycle_id}: {exc}",
                    cycle_id=cycle_id,
                    monitoring_status=MonitoringStatus.INCONCLUSIVE,
                    error_code=result.error_code,
                )
                self._record_cycle_result(result, error=str(exc))
                return result

    def _record_cycle_result(
        self, result: MonitoringCycleResult, *, error: Optional[str]
    ) -> None:
        with self._state_lock:
            self._last_cycle_result = result
            self._current_error = error
            if result.overall_status == MonitoringStatus.STORE_UNAVAILABLE:
                self._state_store_accessible = False
            else:
                self._state_store_accessible = True

    def _install_signal_handlers(self) -> Dict[int, Any]:
        """Install Windows/POSIX termination signal handlers if on main thread."""
        if threading.current_thread() is not threading.main_thread():
            return {}
        previous: Dict[int, Any] = {}
        sigs = [signal.SIGINT, signal.SIGTERM]
        if hasattr(signal, "SIGBREAK"):
            sigs.append(signal.SIGBREAK)

        def _handle_signal(signum: int, _frame: Any) -> None:
            self._log(
                logging.INFO,
                f"Received termination signal {signum}; requesting controlled stop",
            )
            self._stop_event.set()

        for sig in sigs:
            try:
                previous[sig] = signal.getsignal(sig)
                signal.signal(sig, _handle_signal)
            except (OSError, ValueError):
                pass
        return previous

    @staticmethod
    def _restore_signal_handlers(previous: Dict[int, Any]) -> None:
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (OSError, ValueError):
                pass

    def run(
        self,
        *,
        max_cycles: Optional[int] = None,
        install_signals: bool = True,
    ) -> int:
        """Start the daemon and execute the periodic monitoring loop until stopped."""
        prev_signals: Dict[int, Any] = {}
        try:
            self.start()
        except WatchdogAlreadyRunningError:
            return EXIT_ALREADY_RUNNING
        except WatchdogDaemonError:
            return EXIT_FATAL_ERROR

        if install_signals:
            prev_signals = self._install_signal_handlers()

        cycles_executed = 0
        try:
            while not self._stop_event.is_set():
                self.run_once()
                cycles_executed += 1
                if max_cycles is not None and cycles_executed >= max_cycles:
                    break
                # Interruptible wait for next cycle
                if self._stop_event.wait(timeout=self.config.poll_interval_sec):
                    break
        finally:
            if prev_signals:
                self._restore_signal_handlers(prev_signals)
            clean = self.stop()

        return EXIT_CLEAN if clean else EXIT_UNCLEAN_SHUTDOWN


# ---------------------------------------------------------------------------
# Detached Independent Process Spawner
# ---------------------------------------------------------------------------


def spawn_detached_watchdog_daemon(
    config: WatchdogConfig,
    *,
    python_executable: Optional[str] = None,
    cwd: Optional[Union[str, Path]] = None,
    extra_args: Optional[Sequence[str]] = None,
) -> subprocess.Popen[bytes]:
    """Spawn WatchdogDaemon as an independent detached process on Windows.

    Requires `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW |
    CREATE_BREAKAWAY_FROM_JOB` on Windows so the daemon is guaranteed to break
    away from any parent Job Object and survive parent termination.

    Does NOT silently fall back to spawning without `CREATE_BREAKAWAY_FROM_JOB`:
    if the parent process is inside a restrictive Job Object that denies
    breakaway (`ERROR_ACCESS_DENIED`), raises `WatchdogSpawnError` with an
    explicit diagnostic recommending launch via an external supervisor outside
    the Desktop Node's Job Object.
    """
    exe = python_executable or sys.executable
    cmd: List[str] = [
        exe,
        "-m",
        "desktop_worker.watchdog.watchdog_daemon",
        "--worker-id",
        config.worker_id,
        "--db-path",
        str(config.db_path),
        "--poll-interval",
        str(config.poll_interval_sec),
        "--heartbeat-interval",
        str(config.heartbeat_interval_sec),
        "--heartbeat-timeout",
        str(config.heartbeat_timeout_sec),
    ]
    if extra_args:
        cmd.extend(extra_args)

    if sys.platform != "win32":
        return subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

    required_flags = (
        DETACHED_PROCESS
        | CREATE_NEW_PROCESS_GROUP
        | CREATE_NO_WINDOW
        | CREATE_BREAKAWAY_FROM_JOB
    )
    try:
        return subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=required_flags,
            close_fds=True,
        )
    except OSError as exc:
        if getattr(exc, "winerror", None) == ERROR_ACCESS_DENIED:
            msg = (
                "Cannot guarantee independent WatchdogDaemon process: parent Job Object "
                "denied CREATE_BREAKAWAY_FROM_JOB (ERROR_ACCESS_DENIED=5). "
                "Refusing insecure fallback without breakaway. Launch WatchdogDaemon via "
                "an external supervisor (e.g., Windows Service, Task Scheduler, or a "
                "launcher outside the Desktop Node Job Object)."
            )
            logger.error(msg)
            raise WatchdogSpawnError(msg) from exc
        raise WatchdogSpawnError(f"Failed to spawn detached watchdog daemon: {exc}") from exc


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DCF Local Watchdog Daemon")
    parser.add_argument("--worker-id", required=True, help="Unique Desktop Node worker_id")
    parser.add_argument("--db-path", required=True, help="Path to local SQLite State Store")
    parser.add_argument(
        "--poll-interval", type=float, required=True, help="WATCHDOG_POLL_INTERVAL_SEC"
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        required=True,
        help="WATCHDOG_HEARTBEAT_INTERVAL_SEC",
    )
    parser.add_argument(
        "--heartbeat-timeout",
        type=float,
        required=True,
        help="WATCHDOG_HEARTBEAT_TIMEOUT_SEC",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Optional maximum monitoring cycles (for testing)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        config = WatchdogConfig(
            worker_id=args.worker_id,
            db_path=args.db_path,
            poll_interval_sec=args.poll_interval,
            heartbeat_interval_sec=args.heartbeat_interval,
            heartbeat_timeout_sec=args.heartbeat_timeout,
        )
    except WatchdogConfigError as exc:
        logger.error("Invalid WatchdogDaemon configuration: %s", exc)
        return EXIT_FATAL_ERROR

    daemon = WatchdogDaemon(config)
    return daemon.run(max_cycles=args.max_cycles)


if __name__ == "__main__":
    sys.exit(main())
