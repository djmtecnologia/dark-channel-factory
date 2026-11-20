"""Cross-process Windows Job Object handle transfer and authorization (Sprint 2.7 - Task 6).

Provides safe, leak-free duplication of Win32 Job Object handles from an
independent Launcher process into an independent Watchdog process using
``DuplicateHandle``, kernel object identity verification using
``CompareObjectHandles``, handle liveness checks via ``GetHandleInformation``,
and a local authenticated IPC channel with kernel-verified peer identity and
HMAC-SHA256 + nonce replay protection for runtime handle registration and
recovery after Watchdog restarts.

Security invariants:
- Raw Win32 HANDLE numbers are NEVER persisted to SQLite or disk as durable
  kernel object identities.
- Handles are duplicated with least-privilege access rights
  (``JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE``) and ``bInheritHandle=False``.
- Every intermediate process handle and owned duplicated handle is closed
  deterministically to prevent handle leaks.
- Client ``source_pid`` claims are NEVER trusted; the server resolves the real
  connecting process PID and creation time from the Windows kernel TCP table
  (``GetExtendedTcpTable`` + ``GetProcessTimes``).
- Requests are authenticated via HMAC-SHA256 with a shared secret (never sent
  in plaintext and never logged) plus single-use nonce and timestamp window
  to prevent replay attacks.
- After a Watchdog restart, the in-memory authorization registry starts empty
  (fail-closed) until the Launcher re-duplicates and re-registers the handle.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import logging
import os
import secrets
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Win32 Access Rights & Constants
PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
JOB_OBJECT_QUERY = 0x0004
JOB_OBJECT_TERMINATE = 0x0008
JOB_OBJECT_WATCHDOG_ACCESS = JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE
DUPLICATE_CLOSE_SOURCE = 0x00000001
DUPLICATE_SAME_ACCESS = 0x00000002
ERROR_INVALID_HANDLE = 6
ERROR_INSUFFICIENT_BUFFER = 122
STILL_ACTIVE = 259

# iphlpapi TCP table constants
AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5

# Default IPC replay protection window
DEFAULT_MAX_REQUEST_AGE_SEC = 30.0


class JobHandleTransferError(RuntimeError):
    """Raised when duplicating or transferring a Job Object handle fails."""


@dataclass(frozen=True)
class PeerProcessIdentity:
    """Kernel-verified identity of the local process connected over IPC."""

    pid: int
    process_create_time: Optional[int] = None


@dataclass(frozen=True)
class WatchdogPingInfo:
    """Identity snapshot returned by Watchdog IPC PING."""

    watchdog_pid: int
    watchdog_create_time: Optional[int] = None
    daemon_instance_id: Optional[str] = None


@dataclass(frozen=True)
class AuthorizedJobHandle:
    """In-memory binding between an execution attempt and its authorized Job Object handle.

    This record is strictly ephemeral (process-local) and MUST NEVER be
    persisted as a durable kernel object identity.
    """

    execution_id: str
    job_handle: int
    owned_by_registry: bool = False
    attempt_id: Optional[str] = None
    fencing_token: Optional[int] = None
    source_pid: Optional[int] = None
    source_process_create_time: Optional[int] = None
    watchdog_pid: Optional[int] = None
    watchdog_process_create_time: Optional[int] = None
    daemon_instance_id: Optional[str] = None
    registered_at_utc: str = ""


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


def _filetime_to_int(ft: _FILETIME) -> int:
    return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)


def compute_ipc_signature(auth_token: str, payload: Dict[str, Any]) -> str:
    """Compute HMAC-SHA256 hex digest over canonical request fields."""
    signable = {
        k: v
        for k, v in payload.items()
        if k not in ("signature", "auth_token")
    }
    canonical = json.dumps(
        signable,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hmac.new(
        auth_token.encode("utf-8"),
        canonical,
        hashlib.sha256,
    ).hexdigest()


class Win32HandleTransferApi:
    """Native Win32 ctypes wrapper for DuplicateHandle, CompareObjectHandles,
    GetHandleInformation, GetProcessHandleCount, GetProcessTimes,
    GetExitCodeProcess, GetExtendedTcpTable, and CloseHandle."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise JobHandleTransferError("Win32HandleTransferApi is only supported on Windows")

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernelbase = ctypes.WinDLL("kernelbase", use_last_error=True)
        self._iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)

        self._kernel32.GetCurrentProcess.argtypes = []
        self._kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        self._kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE

        self._kernel32.DuplicateHandle.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self._kernel32.DuplicateHandle.restype = wintypes.BOOL

        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

        self._kernel32.GetHandleInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.GetHandleInformation.restype = wintypes.BOOL

        self._kernel32.GetProcessHandleCount.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.GetProcessHandleCount.restype = wintypes.BOOL

        self._kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.GetExitCodeProcess.restype = wintypes.BOOL

        self._kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        self._kernel32.GetProcessTimes.restype = wintypes.BOOL

        self._kernelbase.CompareObjectHandles.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self._kernelbase.CompareObjectHandles.restype = wintypes.BOOL

        self._iphlpapi.GetExtendedTcpTable.argtypes = [
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
            wintypes.ULONG,
            wintypes.DWORD,
            wintypes.ULONG,
        ]
        self._iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD

    def get_current_process(self) -> int:
        return int(self._kernel32.GetCurrentProcess())

    def open_process(self, desired_access: int, inherit: bool, pid: int) -> Tuple[int, int]:
        ctypes.set_last_error(0)
        handle = self._kernel32.OpenProcess(
            wintypes.DWORD(desired_access),
            wintypes.BOOL(inherit),
            wintypes.DWORD(pid),
        )
        return int(handle or 0), int(ctypes.get_last_error())

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
        ctypes.set_last_error(0)
        dup_handle = wintypes.HANDLE(0)
        target_ptr = ctypes.byref(dup_handle) if target_process else None
        ok = bool(
            self._kernel32.DuplicateHandle(
                wintypes.HANDLE(int(source_process)),
                wintypes.HANDLE(int(source_handle)),
                wintypes.HANDLE(int(target_process)) if target_process else wintypes.HANDLE(0),
                target_ptr,
                wintypes.DWORD(desired_access),
                wintypes.BOOL(inherit),
                wintypes.DWORD(options),
            )
        )
        err = int(ctypes.get_last_error())
        return ok, int(dup_handle.value or 0), err

    def close_handle(self, handle: int) -> bool:
        if not handle or int(handle) <= 0:
            return False
        ctypes.set_last_error(0)
        return bool(self._kernel32.CloseHandle(wintypes.HANDLE(int(handle))))

    def get_handle_information(self, handle: int) -> Tuple[bool, int, int]:
        if not handle or int(handle) <= 0:
            return False, 0, ERROR_INVALID_HANDLE
        ctypes.set_last_error(0)
        flags = wintypes.DWORD(0)
        ok = bool(
            self._kernel32.GetHandleInformation(
                wintypes.HANDLE(int(handle)),
                ctypes.byref(flags),
            )
        )
        return ok, int(flags.value), int(ctypes.get_last_error())

    def is_handle_valid(self, handle: int) -> bool:
        """Return True iff ``handle`` is a live, open handle in this process."""
        ok, _flags, _err = self.get_handle_information(handle)
        if not ok:
            return False
        return self.compare_object_handles(handle, handle)

    def compare_object_handles(self, handle_a: int, handle_b: int) -> bool:
        """Return True iff both handles are valid and refer to the same kernel object."""
        if not handle_a or not handle_b or int(handle_a) <= 0 or int(handle_b) <= 0:
            return False
        ctypes.set_last_error(0)
        return bool(
            self._kernelbase.CompareObjectHandles(
                wintypes.HANDLE(int(handle_a)),
                wintypes.HANDLE(int(handle_b)),
            )
        )

    def get_process_handle_count(self, process_handle: Optional[int] = None) -> Tuple[bool, int, int]:
        target = process_handle if process_handle is not None else self.get_current_process()
        ctypes.set_last_error(0)
        count = wintypes.DWORD(0)
        ok = bool(
            self._kernel32.GetProcessHandleCount(
                wintypes.HANDLE(int(target)),
                ctypes.byref(count),
            )
        )
        return ok, int(count.value), int(ctypes.get_last_error())

    def is_process_alive(self, process_handle: int) -> bool:
        """Return True iff ``process_handle`` refers to a live (STILL_ACTIVE) process."""
        if not process_handle or int(process_handle) <= 0:
            return False
        ctypes.set_last_error(0)
        exit_code = wintypes.DWORD(0)
        ok = bool(
            self._kernel32.GetExitCodeProcess(
                wintypes.HANDLE(int(process_handle)),
                ctypes.byref(exit_code),
            )
        )
        return ok and int(exit_code.value) == STILL_ACTIVE

    def get_process_create_time_by_handle(self, process_handle: int) -> Optional[int]:
        """Return 64-bit FILETIME creation timestamp for ``process_handle``, or None."""
        if not process_handle or int(process_handle) <= 0:
            return None
        ft_create = _FILETIME()
        ft_exit = _FILETIME()
        ft_kernel = _FILETIME()
        ft_user = _FILETIME()
        ctypes.set_last_error(0)
        ok = bool(
            self._kernel32.GetProcessTimes(
                wintypes.HANDLE(int(process_handle)),
                ctypes.byref(ft_create),
                ctypes.byref(ft_exit),
                ctypes.byref(ft_kernel),
                ctypes.byref(ft_user),
            )
        )
        if not ok:
            return None
        return _filetime_to_int(ft_create)

    def get_current_process_create_time(self) -> Optional[int]:
        return self.get_process_create_time_by_handle(self.get_current_process())

    def get_process_create_time(self, pid: int) -> Optional[int]:
        """Open ``pid`` with PROCESS_QUERY_LIMITED_INFORMATION and read its create time."""
        if not pid or int(pid) <= 0:
            return None
        proc_h, _err = self.open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not proc_h:
            return None
        try:
            if not self.is_process_alive(proc_h):
                return None
            return self.get_process_create_time_by_handle(proc_h)
        finally:
            self.close_handle(proc_h)

    def resolve_tcp_peer_pid(
        self,
        *,
        client_ip: str,
        client_port: int,
        server_ip: str,
        server_port: int,
    ) -> Optional[int]:
        """Resolve the OS kernel owning PID of a local IPv4 TCP connection."""
        try:
            packed_client_ip = struct.unpack("<I", socket.inet_aton(client_ip))[0]
            packed_server_ip = struct.unpack("<I", socket.inet_aton(server_ip))[0]
        except OSError:
            return None

        size = wintypes.DWORD(0)
        ret = self._iphlpapi.GetExtendedTcpTable(
            None,
            ctypes.byref(size),
            False,
            AF_INET,
            TCP_TABLE_OWNER_PID_ALL,
            0,
        )
        if ret not in (0, ERROR_INSUFFICIENT_BUFFER) or size.value == 0:
            return None

        # Retry up to 3 times if table grows between calls
        buf = None
        for _ in range(3):
            buf = ctypes.create_string_buffer(size.value)
            ret = self._iphlpapi.GetExtendedTcpTable(
                buf,
                ctypes.byref(size),
                False,
                AF_INET,
                TCP_TABLE_OWNER_PID_ALL,
                0,
            )
            if ret == 0:
                break
            if ret != ERROR_INSUFFICIENT_BUFFER:
                return None
        if ret != 0 or buf is None:
            return None

        num_entries = struct.unpack_from("<I", buf.raw, 0)[0]
        row_size = ctypes.sizeof(_MIB_TCPROW_OWNER_PID)
        offset = ctypes.sizeof(wintypes.DWORD)

        for idx in range(num_entries):
            pos = offset + idx * row_size
            if pos + row_size > len(buf.raw):
                break
            row = _MIB_TCPROW_OWNER_PID.from_buffer_copy(buf.raw, pos)
            row_local_port = socket.ntohs(row.dwLocalPort & 0xFFFF)
            row_remote_port = socket.ntohs(row.dwRemotePort & 0xFFFF)
            if (
                row.dwLocalAddr == packed_client_ip
                and row_local_port == int(client_port)
                and row.dwRemoteAddr == packed_server_ip
                and row_remote_port == int(server_port)
            ):
                return int(row.dwOwningPid)
        return None

    def resolve_peer_identity(self, conn: socket.socket) -> Optional[PeerProcessIdentity]:
        """Resolve kernel-verified PID and creation time for an accepted TCP socket."""
        try:
            client_ip, client_port = conn.getpeername()[:2]
            server_ip, server_port = conn.getsockname()[:2]
        except OSError:
            return None

        pid = self.resolve_tcp_peer_pid(
            client_ip=str(client_ip),
            client_port=int(client_port),
            server_ip=str(server_ip),
            server_port=int(server_port),
        )
        if pid is None or pid <= 0:
            return None

        create_time = self.get_process_create_time(pid)
        return PeerProcessIdentity(pid=pid, process_create_time=create_time)


def close_remote_handle(
    target_pid: int,
    remote_handle: int,
    *,
    expected_target_create_time: Optional[int] = None,
    api: Optional[Win32HandleTransferApi] = None,
) -> bool:
    """Close a handle inside ``target_pid``'s handle table using DUPLICATE_CLOSE_SOURCE.

    Used as a leak-prevention cleanup if ``DuplicateHandle`` succeeded but
    ``REGISTER_HANDLE`` failed before the Watchdog took ownership.
    """
    if not target_pid or int(target_pid) <= 0 or not remote_handle or int(remote_handle) <= 0:
        return False

    transfer_api = api or Win32HandleTransferApi()
    access = PROCESS_DUP_HANDLE | PROCESS_QUERY_LIMITED_INFORMATION
    target_proc_handle, _err = transfer_api.open_process(access, False, int(target_pid))
    if not target_proc_handle:
        return False
    try:
        if not transfer_api.is_process_alive(target_proc_handle):
            return False
        if expected_target_create_time is not None:
            actual_ct = transfer_api.get_process_create_time_by_handle(target_proc_handle)
            if actual_ct is None or actual_ct != expected_target_create_time:
                return False
        ok, _dup, _dup_err = transfer_api.duplicate_handle(
            target_proc_handle,
            int(remote_handle),
            0,
            desired_access=0,
            inherit=False,
            options=DUPLICATE_CLOSE_SOURCE,
        )
        return bool(ok)
    finally:
        transfer_api.close_handle(target_proc_handle)


def duplicate_job_handle_to_process(
    source_job_handle: int,
    target_pid: int,
    *,
    desired_access: int = JOB_OBJECT_WATCHDOG_ACCESS,
    expected_target_create_time: Optional[int] = None,
    api: Optional[Win32HandleTransferApi] = None,
) -> int:
    """Duplicate a live Job Object handle from this process into ``target_pid``.

    Validates that ``target_pid`` is alive and matches ``expected_target_create_time``
    (preventing PID reuse) both BEFORE and AFTER ``DuplicateHandle``.

    The returned integer is a HANDLE valid in ``target_pid``'s handle table
    (NOT in the caller's handle table). The caller must NOT call CloseHandle on
    the returned value; ``target_pid`` owns it and must close it when done.
    """
    if source_job_handle is None or int(source_job_handle) <= 0:
        raise JobHandleTransferError("source_job_handle must be a positive integer handle")
    if target_pid is None or int(target_pid) <= 0:
        raise JobHandleTransferError("target_pid must be a positive process ID")

    # Enforce least-privilege: never allow DUPLICATE_SAME_ACCESS or excessive rights
    if desired_access & ~JOB_OBJECT_WATCHDOG_ACCESS:
        raise JobHandleTransferError(
            f"desired_access (0x{desired_access:x}) exceeds least-privilege "
            f"JOB_OBJECT_WATCHDOG_ACCESS (0x{JOB_OBJECT_WATCHDOG_ACCESS:x})"
        )

    transfer_api = api or Win32HandleTransferApi()

    if not transfer_api.is_handle_valid(int(source_job_handle)):
        raise JobHandleTransferError(
            f"source_job_handle ({source_job_handle}) is closed or invalid"
        )

    target_proc_handle, open_err = transfer_api.open_process(
        PROCESS_DUP_HANDLE | PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        int(target_pid),
    )
    if not target_proc_handle:
        raise JobHandleTransferError(
            f"OpenProcess(PROCESS_DUP_HANDLE, pid={target_pid}) failed (win32_err={open_err})"
        )

    try:
        # Pre-duplication liveness & PID-reuse validation
        if not transfer_api.is_process_alive(target_proc_handle):
            raise JobHandleTransferError(
                f"Target process pid={target_pid} is no longer alive before DuplicateHandle"
            )
        if expected_target_create_time is not None:
            actual_ct = transfer_api.get_process_create_time_by_handle(target_proc_handle)
            if actual_ct is None or actual_ct != expected_target_create_time:
                raise JobHandleTransferError(
                    f"Target process pid={target_pid} creation time mismatch before "
                    f"DuplicateHandle (expected={expected_target_create_time}, actual={actual_ct}; "
                    "possible PID reuse)"
                )

        current_proc = transfer_api.get_current_process()
        ok, dup_handle, dup_err = transfer_api.duplicate_handle(
            current_proc,
            int(source_job_handle),
            target_proc_handle,
            desired_access=desired_access,
            inherit=False,
            options=0,
        )
        if not ok or dup_handle <= 0:
            raise JobHandleTransferError(
                f"DuplicateHandle to pid={target_pid} failed (win32_err={dup_err})"
            )

        # Post-duplication liveness & PID-reuse verification
        if not transfer_api.is_process_alive(target_proc_handle):
            raise JobHandleTransferError(
                f"Target process pid={target_pid} exited immediately after DuplicateHandle"
            )
        if expected_target_create_time is not None:
            post_ct = transfer_api.get_process_create_time_by_handle(target_proc_handle)
            if post_ct is None or post_ct != expected_target_create_time:
                # Best-effort cleanup of duplicated handle if target identity diverged
                transfer_api.duplicate_handle(
                    target_proc_handle,
                    dup_handle,
                    0,
                    desired_access=0,
                    inherit=False,
                    options=DUPLICATE_CLOSE_SOURCE,
                )
                raise JobHandleTransferError(
                    f"Target process pid={target_pid} creation time diverged after DuplicateHandle"
                )

        return dup_handle
    finally:
        transfer_api.close_handle(target_proc_handle)


def duplicate_job_handle_in_process(
    source_job_handle: int,
    *,
    desired_access: int = JOB_OBJECT_WATCHDOG_ACCESS,
    api: Optional[Win32HandleTransferApi] = None,
) -> int:
    """Duplicate a live Job Object handle within the current process."""
    if source_job_handle is None or int(source_job_handle) <= 0:
        raise JobHandleTransferError("source_job_handle must be a positive integer handle")
    if desired_access & ~JOB_OBJECT_WATCHDOG_ACCESS:
        raise JobHandleTransferError(
            f"desired_access (0x{desired_access:x}) exceeds least-privilege "
            f"JOB_OBJECT_WATCHDOG_ACCESS (0x{JOB_OBJECT_WATCHDOG_ACCESS:x})"
        )

    transfer_api = api or Win32HandleTransferApi()
    if not transfer_api.is_handle_valid(int(source_job_handle)):
        raise JobHandleTransferError(
            f"source_job_handle ({source_job_handle}) is closed or invalid"
        )

    current_proc = transfer_api.get_current_process()
    ok, dup_handle, dup_err = transfer_api.duplicate_handle(
        current_proc,
        int(source_job_handle),
        current_proc,
        desired_access=desired_access,
        inherit=False,
        options=0,
    )
    if not ok or dup_handle <= 0:
        raise JobHandleTransferError(
            f"DuplicateHandle (in-process) failed (win32_err={dup_err})"
        )
    return dup_handle


# ---------------------------------------------------------------------------
# Local Authenticated IPC Channel (Launcher <-> Independent Watchdog)
# ---------------------------------------------------------------------------


class JobHandleTransferServer:
    """Local loopback IPC server hosted by the Watchdog process to receive
    duplicated Job Object handles from the independent Launcher process.

    Security controls:
    - HMAC-SHA256 authentication using ``auth_token`` (constant-time comparison;
      secret never transmitted in plaintext and never logged).
    - Single-use ``nonce`` + ``timestamp_utc`` freshness window (replay protection).
    - OS kernel peer identity verification via ``GetExtendedTcpTable`` +
      ``GetProcessTimes``; client-supplied ``source_pid`` is never trusted and
      spoofing is rejected.
    - Mandatory (fail-closed) pinning to ``expected_launcher_pid`` and
      ``expected_launcher_create_time`` to block unauthorized local processes
      and PID reuse; identity rotation is only possible via authenticated
      ``reauthorize_launcher()`` with mandatory Windows kernel verification.
    """

    def __init__(
        self,
        *,
        on_register: Callable[..., Tuple[bool, str]],
        on_unregister: Callable[[str], bool],
        auth_token: Optional[str] = None,
        host: str = "127.0.0.1",
        port: int = 0,
        daemon_instance_id: Optional[str] = None,
        expected_launcher_pid: Optional[int] = None,
        expected_launcher_create_time: Optional[int] = None,
        peer_identity_resolver: Optional[Callable[[socket.socket], Optional[PeerProcessIdentity]]] = None,
        max_request_age_sec: float = DEFAULT_MAX_REQUEST_AGE_SEC,
        clock: Optional[Callable[[], float]] = None,
        api: Optional[Win32HandleTransferApi] = None,
    ) -> None:
        if auth_token is not None and (not isinstance(auth_token, str) or not auth_token.strip()):
            raise ValueError("auth_token must be a non-empty string")
        if (
            expected_launcher_pid is None
            or isinstance(expected_launcher_pid, bool)
            or not isinstance(expected_launcher_pid, int)
            or expected_launcher_pid <= 0
        ):
            raise ValueError(
                "expected_launcher_pid is required and must be a positive integer (fail-closed)"
            )
        if (
            expected_launcher_create_time is None
            or isinstance(expected_launcher_create_time, bool)
            or not isinstance(expected_launcher_create_time, int)
            or expected_launcher_create_time <= 0
        ):
            raise ValueError(
                "expected_launcher_create_time is required and must be a positive integer (fail-closed)"
            )

        self._on_register = on_register
        self._on_unregister = on_unregister
        self._auth_token = auth_token or secrets.token_hex(32)
        self._host = host
        self._port = int(port)
        self._daemon_instance_id = (
            daemon_instance_id.strip()
            if isinstance(daemon_instance_id, str) and daemon_instance_id.strip()
            else f"ipc-{secrets.token_hex(6)}"
        )
        self._expected_launcher_pid: int = int(expected_launcher_pid)
        self._expected_launcher_create_time: int = int(expected_launcher_create_time)
        self._identity_lock = threading.Lock()
        self._max_request_age_sec = float(max_request_age_sec)
        self._clock = clock or time.time
        self._api = api or (Win32HandleTransferApi() if sys.platform == "win32" else None)
        self._peer_identity_resolver = peer_identity_resolver or (
            self._api.resolve_peer_identity if self._api is not None else None
        )
        self._watchdog_pid: int = os.getpid()
        self._watchdog_create_time: Optional[int] = (
            self._api.get_current_process_create_time() if self._api is not None else None
        )

        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._nonce_lock = threading.Lock()
        self._seen_nonces: Dict[str, float] = {}

    def __repr__(self) -> str:
        return (
            f"JobHandleTransferServer(host={self._host!r}, port={self.port}, "
            f"running={self.is_running}, auth_token='<REDACTED>')"
        )

    @property
    def auth_token(self) -> str:
        return self._auth_token

    @property
    def port(self) -> int:
        if self._sock is None:
            return self._port
        try:
            return int(self._sock.getsockname()[1])
        except OSError:
            return self._port

    @property
    def expected_launcher_identity(self) -> Tuple[int, int]:
        """Return currently authorized (launcher_pid, launcher_create_time)."""
        with self._identity_lock:
            return self._expected_launcher_pid, self._expected_launcher_create_time

    @property
    def watchdog_pid(self) -> int:
        return self._watchdog_pid

    @property
    def watchdog_create_time(self) -> Optional[int]:
        return self._watchdog_create_time

    @property
    def daemon_instance_id(self) -> str:
        return self._daemon_instance_id

    @property
    def is_running(self) -> bool:
        return (
            self._sock is not None
            and self._thread is not None
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )

    def reauthorize_launcher(
        self,
        *,
        new_launcher_pid: int,
        new_launcher_create_time: int,
        auth_token: str,
        reason: str = "launcher_restart",
    ) -> Tuple[int, int]:
        """Explicit, authenticated, fail-closed procedure to rotate the authorized Launcher identity.

        Used when the Launcher process restarts and obtains a new PID /
        creation timestamp. Never triggered automatically by incoming IPC
        requests. Validates ``auth_token`` in constant time, requires a valid
        Windows kernel API, and unconditionally verifies via ``GetProcessTimes``
        that the new Launcher process is alive and matches the provided
        ``new_launcher_create_time`` before atomically swapping the authorized
        identity.
        """
        if self._stop_event.is_set():
            raise JobHandleTransferError("Cannot reauthorize launcher while server is shutting down")
        if not isinstance(auth_token, str) or not hmac.compare_digest(auth_token, self._auth_token):
            raise JobHandleTransferError("Unauthorized launcher reauthorization: invalid auth_token")
        if (
            isinstance(new_launcher_pid, bool)
            or not isinstance(new_launcher_pid, int)
            or new_launcher_pid <= 0
        ):
            raise ValueError("new_launcher_pid must be a positive integer")
        if (
            isinstance(new_launcher_create_time, bool)
            or not isinstance(new_launcher_create_time, int)
            or new_launcher_create_time <= 0
        ):
            raise ValueError("new_launcher_create_time must be a positive integer")

        if self._api is None or not hasattr(self._api, "get_process_create_time"):
            raise JobHandleTransferError(
                "Cannot reauthorize launcher: Windows kernel API is unavailable (fail-closed)"
            )

        actual_ct = self._api.get_process_create_time(int(new_launcher_pid))
        if (
            actual_ct is None
            or isinstance(actual_ct, bool)
            or not isinstance(actual_ct, int)
            or actual_ct <= 0
            or actual_ct != int(new_launcher_create_time)
        ):
            raise JobHandleTransferError(
                f"Kernel verification failed for new launcher pid={new_launcher_pid}: "
                f"expected create_time={new_launcher_create_time}, actual={actual_ct}"
            )

        with self._identity_lock:
            if self._stop_event.is_set():
                raise JobHandleTransferError(
                    "Cannot reauthorize launcher while server is shutting down"
                )
            old_pid = self._expected_launcher_pid
            old_ct = self._expected_launcher_create_time
            self._expected_launcher_pid = int(new_launcher_pid)
            self._expected_launcher_create_time = int(new_launcher_create_time)

        logger.info(
            "Reauthorized Launcher identity (reason=%s, old_pid=%s, old_ct=%s, new_pid=%s, new_ct=%s)",
            reason,
            old_pid,
            old_ct,
            new_launcher_pid,
            new_launcher_create_time,
        )
        return int(new_launcher_pid), int(new_launcher_create_time)

    def start(self) -> Tuple[str, int]:
        with self._identity_lock:
            if (
                self._expected_launcher_pid is None
                or self._expected_launcher_pid <= 0
                or self._expected_launcher_create_time is None
                or self._expected_launcher_create_time <= 0
            ):
                raise JobHandleTransferError(
                    "Cannot start JobHandleTransferServer without expected_launcher_pid "
                    "and expected_launcher_create_time (fail-closed)"
                )
        if self._sock is not None:
            return self._host, self.port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self._host, self._port))
            sock.listen(8)
            sock.settimeout(0.2)
        except OSError:
            sock.close()
            raise
        self._sock = sock
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._serve_loop,
            name="dcf-job-handle-transfer-server",
            daemon=True,
        )
        self._thread.start()
        return self._host, self.port

    def close(self, timeout_sec: float = 2.0) -> bool:
        """Stop accepting connections and wait for the server thread to exit.

        Returns ``True`` iff the server thread has actually exited within
        ``timeout_sec``. Returns ``False`` if the thread is still alive
        (unclean shutdown), preserving ``self._thread`` so callers do not
        release shared resources prematurely.
        """
        self._stop_event.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout_sec)))
            if thread.is_alive():
                logger.error(
                    "JobHandleTransferServer thread did not exit within %.2fs; "
                    "reporting unclean IPC shutdown",
                    float(timeout_sec),
                )
                return False
            self._thread = None
        return True

    def _serve_loop(self) -> None:
        while not self._stop_event.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(2.0)
                try:
                    peer_identity = (
                        self._peer_identity_resolver(conn)
                        if self._peer_identity_resolver is not None
                        else None
                    )
                    raw = b""
                    while not raw.endswith(b"\n") and len(raw) < 65536:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        raw += chunk
                    if not raw:
                        continue
                    req = json.loads(raw.decode("utf-8"))
                    resp = self._handle_request(req, peer_identity=peer_identity)
                except Exception as exc:
                    resp = {"ok": False, "error": f"invalid_request: {type(exc).__name__}"}
                try:
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                except OSError:
                    pass

    def _check_and_record_nonce(self, nonce: Any, timestamp: Any) -> Optional[str]:
        if not isinstance(nonce, str) or len(nonce) < 16 or len(nonce) > 128:
            return "invalid_nonce"
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            return "invalid_timestamp"

        now = float(self._clock())
        age = abs(now - float(timestamp))
        if age > self._max_request_age_sec:
            return "request_expired"

        with self._nonce_lock:
            # Prune expired nonces
            cutoff = now - (self._max_request_age_sec * 2.0)
            expired_keys = [k for k, ts in self._seen_nonces.items() if ts < cutoff]
            for k in expired_keys:
                self._seen_nonces.pop(k, None)

            if nonce in self._seen_nonces:
                return "replay_detected"
            self._seen_nonces[nonce] = now

        return None

    def _validate_peer_identity_locked(
        self, peer_identity: Optional[PeerProcessIdentity]
    ) -> Optional[str]:
        """Validate peer identity against expected launcher identity.

        Caller MUST hold ``self._identity_lock``.
        """
        if peer_identity is None or peer_identity.pid <= 0:
            return "peer_identity_unverifiable"
        if (
            peer_identity.process_create_time is None
            or peer_identity.process_create_time <= 0
        ):
            return "peer_create_time_unverifiable"
        if peer_identity.pid != self._expected_launcher_pid:
            return "unauthorized_launcher_pid"
        if peer_identity.process_create_time != self._expected_launcher_create_time:
            return "launcher_pid_reused_or_mismatch"
        return None

    def _validate_peer_identity(
        self, peer_identity: Optional[PeerProcessIdentity]
    ) -> Optional[str]:
        with self._identity_lock:
            return self._validate_peer_identity_locked(peer_identity)

    def _handle_request(
        self,
        req: Dict[str, Any],
        *,
        peer_identity: Optional[PeerProcessIdentity] = None,
    ) -> Dict[str, Any]:
        if self._stop_event.is_set():
            return {"ok": False, "error": "server_shutting_down"}
        if not isinstance(req, dict):
            return {"ok": False, "error": "request_must_be_object"}

        # 1. Authenticate via HMAC-SHA256 (constant-time, no plaintext token)
        provided_sig = req.get("signature")
        if not isinstance(provided_sig, str) or not provided_sig:
            return {"ok": False, "error": "unauthorized"}
        expected_sig = compute_ipc_signature(self._auth_token, req)
        if not hmac.compare_digest(provided_sig, expected_sig):
            return {"ok": False, "error": "unauthorized"}

        # 2. Replay protection (nonce + timestamp freshness)
        replay_err = self._check_and_record_nonce(req.get("nonce"), req.get("timestamp"))
        if replay_err is not None:
            return {"ok": False, "error": replay_err}

        # 3. Kernel-verified peer process identity (strict fail-closed on ALL ops)
        peer_err = self._validate_peer_identity(peer_identity)
        if peer_err is not None:
            return {"ok": False, "error": peer_err}
        assert peer_identity is not None

        # 4. Reject spoofed source_pid if client supplied one
        claimed_pid = req.get("source_pid")
        if claimed_pid is not None:
            if isinstance(claimed_pid, bool) or not isinstance(claimed_pid, int):
                return {"ok": False, "error": "invalid_source_pid"}
            if int(claimed_pid) != peer_identity.pid:
                return {"ok": False, "error": "spoofed_source_pid"}

        op = req.get("op")
        if op == "PING":
            return {
                "ok": True,
                "watchdog_pid": self._watchdog_pid,
                "watchdog_create_time": self._watchdog_create_time,
                "watchdog_process_create_time": self._watchdog_create_time,
                "daemon_instance_id": self._daemon_instance_id,
            }

        if op == "REGISTER_HANDLE":
            # Bind registration strictly to this Watchdog instance (reject stale PINGs)
            target_wd_pid = req.get("target_watchdog_pid")
            target_wd_ct = req.get("target_watchdog_create_time")
            target_daemon_id = req.get("target_daemon_instance_id")
            if (
                isinstance(target_wd_pid, bool)
                or not isinstance(target_wd_pid, int)
                or int(target_wd_pid) != self._watchdog_pid
            ):
                return {
                    "ok": False,
                    "error": "watchdog_instance_mismatch",
                    "handle_closed_by_server": False,
                }
            if (
                not isinstance(target_daemon_id, str)
                or target_daemon_id != self._daemon_instance_id
            ):
                return {
                    "ok": False,
                    "error": "watchdog_instance_mismatch",
                    "handle_closed_by_server": False,
                }
            if self._watchdog_create_time is not None:
                if (
                    isinstance(target_wd_ct, bool)
                    or not isinstance(target_wd_ct, int)
                    or int(target_wd_ct) != self._watchdog_create_time
                ):
                    return {
                        "ok": False,
                        "error": "watchdog_instance_mismatch",
                        "handle_closed_by_server": False,
                    }

            execution_id = str(req.get("execution_id") or "").strip()
            job_handle = req.get("job_handle")
            attempt_id = req.get("attempt_id")
            fencing_token = req.get("fencing_token")
            if (
                not execution_id
                or isinstance(job_handle, bool)
                or not isinstance(job_handle, int)
                or job_handle <= 0
            ):
                return {"ok": False, "error": "invalid_execution_or_handle"}
            if not isinstance(attempt_id, str) or not attempt_id.strip():
                return {"ok": False, "error": "missing_attempt_id"}
            if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 0:
                return {"ok": False, "error": "invalid_fencing_token"}

            with self._identity_lock:
                if self._stop_event.is_set():
                    return {
                        "ok": False,
                        "error": "server_shutting_down",
                        "handle_closed_by_server": False,
                    }
                recheck_err = self._validate_peer_identity_locked(peer_identity)
                if recheck_err is not None:
                    return {
                        "ok": False,
                        "error": recheck_err,
                        "handle_closed_by_server": False,
                    }
                ok, msg = self._on_register(
                    execution_id,
                    int(job_handle),
                    attempt_id.strip(),
                    int(fencing_token),
                    peer_identity.pid,
                    peer_identity.process_create_time,
                )
            return {
                "ok": bool(ok),
                "message": msg,
                "watchdog_pid": self._watchdog_pid,
                "watchdog_process_create_time": self._watchdog_create_time,
                "daemon_instance_id": self._daemon_instance_id,
                "handle_closed_by_server": not bool(ok),
            }

        if op == "UNREGISTER_HANDLE":
            execution_id = str(req.get("execution_id") or "").strip()
            if not execution_id:
                return {"ok": False, "error": "invalid_execution_id"}
            with self._identity_lock:
                if self._stop_event.is_set():
                    return {"ok": False, "error": "server_shutting_down"}
                recheck_err = self._validate_peer_identity_locked(peer_identity)
                if recheck_err is not None:
                    return {"ok": False, "error": recheck_err}
                removed = self._on_unregister(execution_id)
            return {"ok": True, "removed": bool(removed)}

        return {"ok": False, "error": f"unknown_op: {op}"}


class JobHandleTransferClient:
    """Launcher-side client that duplicates a Job Object handle into the
    independent Watchdog process and registers it over the local IPC channel."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int,
        auth_token: str,
        timeout_sec: float = 3.0,
        api: Optional[Win32HandleTransferApi] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if not isinstance(auth_token, str) or not auth_token.strip():
            raise ValueError("auth_token must be a non-empty string")
        self._host = host
        self._port = int(port)
        self._auth_token = auth_token
        self._timeout_sec = float(timeout_sec)
        self._api = api
        self._clock = clock or time.time

    def __repr__(self) -> str:
        return (
            f"JobHandleTransferClient(host={self._host!r}, port={self._port}, "
            "auth_token='<REDACTED>')"
        )

    def _build_signed_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        req = dict(payload)
        req["nonce"] = secrets.token_hex(16)
        req["timestamp"] = float(self._clock())
        req["signature"] = compute_ipc_signature(self._auth_token, req)
        return req

    def _send_raw(self, req: Dict[str, Any]) -> Dict[str, Any]:
        with socket.create_connection((self._host, self._port), timeout=self._timeout_sec) as sock:
            sock.settimeout(self._timeout_sec)
            sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
            raw = b""
            while not raw.endswith(b"\n") and len(raw) < 65536:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
        if not raw:
            raise JobHandleTransferError("Empty response from Watchdog transfer server")
        return json.loads(raw.decode("utf-8"))

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        req = self._build_signed_request(payload)
        return self._send_raw(req)

    def ping_watchdog(self) -> WatchdogPingInfo:
        resp = self._send({"op": "PING"})
        if not resp.get("ok") or not isinstance(resp.get("watchdog_pid"), int):
            raise JobHandleTransferError(f"Watchdog PING failed: {resp.get('error', resp)}")
        return WatchdogPingInfo(
            watchdog_pid=int(resp["watchdog_pid"]),
            watchdog_create_time=resp.get("watchdog_create_time"),
            daemon_instance_id=resp.get("daemon_instance_id"),
        )

    def ping_watchdog_pid(self) -> int:
        return self.ping_watchdog().watchdog_pid

    def transfer_and_register(
        self,
        *,
        execution_id: str,
        source_job_handle: int,
        attempt_id: str,
        fencing_token: int,
        desired_access: int = JOB_OBJECT_WATCHDOG_ACCESS,
    ) -> Tuple[int, int]:
        """Duplicate ``source_job_handle`` into Watchdog and register it.

        If registration fails after ``DuplicateHandle`` succeeded, attempts to
        close the duplicated handle inside Watchdog to prevent handle leaks.

        Returns ``(watchdog_pid, duplicated_handle_in_watchdog)``.
        """
        ping_info = self.ping_watchdog()
        dup_handle = duplicate_job_handle_to_process(
            source_job_handle,
            ping_info.watchdog_pid,
            desired_access=desired_access,
            expected_target_create_time=ping_info.watchdog_create_time,
            api=self._api,
        )
        try:
            resp = self._send(
                {
                    "op": "REGISTER_HANDLE",
                    "execution_id": execution_id,
                    "job_handle": dup_handle,
                    "attempt_id": attempt_id,
                    "fencing_token": fencing_token,
                    "source_pid": os.getpid(),
                    "target_watchdog_pid": ping_info.watchdog_pid,
                    "target_watchdog_create_time": ping_info.watchdog_create_time,
                    "target_daemon_instance_id": ping_info.daemon_instance_id,
                }
            )
        except Exception as exc:
            close_remote_handle(
                ping_info.watchdog_pid,
                dup_handle,
                expected_target_create_time=ping_info.watchdog_create_time,
                api=self._api,
            )
            raise JobHandleTransferError(
                f"IPC error during REGISTER_HANDLE (remote handle cleaned up): {exc}"
            ) from exc

        if not resp.get("ok"):
            if not resp.get("handle_closed_by_server"):
                close_remote_handle(
                    ping_info.watchdog_pid,
                    dup_handle,
                    expected_target_create_time=ping_info.watchdog_create_time,
                    api=self._api,
                )
            raise JobHandleTransferError(
                f"Watchdog rejected duplicated handle registration: {resp.get('error') or resp.get('message')}"
            )
        return ping_info.watchdog_pid, dup_handle

    def unregister(self, execution_id: str) -> bool:
        resp = self._send({"op": "UNREGISTER_HANDLE", "execution_id": execution_id})
        return bool(resp.get("ok"))
