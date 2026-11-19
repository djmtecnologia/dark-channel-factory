"""Isolated Windows Job Object process manager using ctypes.

Implements secure suspended process creation, Job Object association,
association validation, identity tracking, active process querying with
truncation detection, controlled termination verification, and leak-free
handle management.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Win32 Constants
# ---------------------------------------------------------------------------

CREATE_SUSPENDED = 0x00000004
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000

FORBIDDEN_BREAKAWAY_FLAGS = (
    JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
)

JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

ERROR_MORE_DATA = 234
ERROR_ACCESS_DENIED = 5
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
STILL_ACTIVE = 259


# ---------------------------------------------------------------------------
# Win32 Structures
# ---------------------------------------------------------------------------

class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
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


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
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


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def _make_process_id_list_struct(capacity: int) -> type[ctypes.Structure]:
    """Build a JOBOBJECT_BASIC_PROCESS_ID_LIST structure for `capacity` PIDs."""

    class JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
        _fields_ = [
            ("NumberOfAssignedProcesses", wintypes.DWORD),
            ("NumberOfProcessIdsInList", wintypes.DWORD),
            ("ProcessIdList", ctypes.c_size_t * capacity),
        ]

    return JOBOBJECT_BASIC_PROCESS_ID_LIST


# ---------------------------------------------------------------------------
# Enums & Data Models
# ---------------------------------------------------------------------------

class LauncherState(str, Enum):
    INIT = "INIT"
    JOB_CREATED = "JOB_CREATED"
    JOB_CONFIGURED = "JOB_CONFIGURED"
    CREATED_SUSPENDED = "CREATED_SUSPENDED"
    ASSOCIATED = "ASSOCIATED"
    VALIDATED = "VALIDATED"
    RUNNING = "RUNNING"
    ABORTED = "ABORTED"
    TERMINATED = "TERMINATED"


class TerminationStatus(str, Enum):
    TREE_TERMINATED = "TREE_TERMINATED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class JobObjectError:
    """Structured error returned on launch or operation failure."""

    stage: str
    code: str
    message: str
    winerror: Optional[int] = None
    aborted_cleanly: bool = True


@dataclass
class ProcessIdentity:
    """Immutable-intent identity record to prevent PID reuse confusion."""

    pid: int
    process_handle: Optional[int]
    thread_handle: Optional[int]
    process_creation_time: Optional[int]
    job_handle: Optional[int]
    process_name: str
    command_line: str
    created_at: float
    state: LauncherState = LauncherState.INIT


@dataclass(frozen=True)
class BasicAccountingInfo:
    active_processes: int
    total_processes: int
    total_terminated_processes: int


@dataclass(frozen=True)
class ProcessIdListResult:
    ok: bool
    pids: List[int] = field(default_factory=list)
    assigned_count: int = 0
    returned_count: int = 0
    truncated: bool = False
    error_code: Optional[int] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class TerminationResult:
    status: TerminationStatus
    active_processes: Optional[int] = None
    pids: Optional[List[int]] = None
    truncated: bool = False
    reason: str = ""
    error: Optional[JobObjectError] = None


# ---------------------------------------------------------------------------
# Win32 API Wrapper (injectable for unit tests)
# ---------------------------------------------------------------------------

class Win32Api:
    """Thin ctypes wrapper around kernel32 Job Object and Process APIs."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Win32Api is only available on Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        k32 = self._kernel32

        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE

        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        k32.SetInformationJobObject.restype = wintypes.BOOL

        k32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        k32.CreateProcessW.restype = wintypes.BOOL

        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL

        k32.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        k32.IsProcessInJob.restype = wintypes.BOOL

        k32.ResumeThread.argtypes = [wintypes.HANDLE]
        k32.ResumeThread.restype = wintypes.DWORD

        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess.restype = wintypes.BOOL

        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateJobObject.restype = wintypes.BOOL

        k32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.QueryInformationJobObject.restype = wintypes.BOOL

        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL

        k32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        k32.GetProcessTimes.restype = wintypes.BOOL

        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD

        k32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.GetExitCodeProcess.restype = wintypes.BOOL

    def get_last_error(self) -> int:
        return ctypes.get_last_error()

    def create_job_object(self, name: Optional[str] = None) -> Optional[int]:
        handle = self._kernel32.CreateJobObjectW(None, name)
        return int(handle) if handle else None

    def set_information_job_object(
        self, job_handle: int, info_class: int, info_struct: ctypes.Structure
    ) -> bool:
        return bool(
            self._kernel32.SetInformationJobObject(
                wintypes.HANDLE(job_handle),
                info_class,
                ctypes.byref(info_struct),
                ctypes.sizeof(info_struct),
            )
        )

    def create_process_suspended(
        self,
        command_line: str,
        working_dir: Optional[str] = None,
        creation_flags: int = 0,
    ) -> Optional[PROCESS_INFORMATION]:
        if creation_flags & CREATE_BREAKAWAY_FROM_JOB:
            raise ValueError("CREATE_BREAKAWAY_FROM_JOB is forbidden in creation_flags.")

        startup_info = STARTUPINFOW()
        startup_info.cb = ctypes.sizeof(STARTUPINFOW)
        proc_info = PROCESS_INFORMATION()

        cmd_buf = ctypes.create_unicode_buffer(command_line)
        flags = creation_flags | CREATE_SUSPENDED

        ok = self._kernel32.CreateProcessW(
            None,
            cmd_buf,
            None,
            None,
            False,
            flags,
            None,
            working_dir,
            ctypes.byref(startup_info),
            ctypes.byref(proc_info),
        )
        if not ok:
            return None
        return proc_info

    def assign_process_to_job_object(
        self, job_handle: int, process_handle: int
    ) -> bool:
        return bool(
            self._kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(job_handle), wintypes.HANDLE(process_handle)
            )
        )

    def is_process_in_job(
        self, process_handle: int, job_handle: int
    ) -> tuple[bool, bool]:
        in_job = wintypes.BOOL(False)
        ok = self._kernel32.IsProcessInJob(
            wintypes.HANDLE(process_handle),
            wintypes.HANDLE(job_handle),
            ctypes.byref(in_job),
        )
        return bool(ok), bool(in_job.value)

    def resume_thread(self, thread_handle: int) -> int:
        return int(self._kernel32.ResumeThread(wintypes.HANDLE(thread_handle)))

    def terminate_process(self, process_handle: int, exit_code: int = 1) -> bool:
        return bool(
            self._kernel32.TerminateProcess(
                wintypes.HANDLE(process_handle), exit_code
            )
        )

    def terminate_job_object(self, job_handle: int, exit_code: int = 1) -> bool:
        return bool(
            self._kernel32.TerminateJobObject(wintypes.HANDLE(job_handle), exit_code)
        )

    def query_basic_accounting(
        self, job_handle: int
    ) -> tuple[bool, Optional[JOBOBJECT_BASIC_ACCOUNTING_INFORMATION], int]:
        info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        ret_len = wintypes.DWORD(0)
        ok = bool(
            self._kernel32.QueryInformationJobObject(
                wintypes.HANDLE(job_handle),
                JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
                ctypes.byref(ret_len),
            )
        )
        err = 0 if ok else self.get_last_error()
        return ok, (info if ok else None), err

    def query_process_id_list(
        self, job_handle: int, capacity: int = 64
    ) -> tuple[bool, Any, int]:
        struct_cls = _make_process_id_list_struct(capacity)
        info = struct_cls()
        ret_len = wintypes.DWORD(0)
        ok = bool(
            self._kernel32.QueryInformationJobObject(
                wintypes.HANDLE(job_handle),
                JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                ctypes.byref(info),
                ctypes.sizeof(info),
                ctypes.byref(ret_len),
            )
        )
        err = 0 if ok else self.get_last_error()
        return ok, info, err

    def get_process_creation_time(self, process_handle: int) -> Optional[int]:
        creation = wintypes.FILETIME()
        exit_t = wintypes.FILETIME()
        kernel_t = wintypes.FILETIME()
        user_t = wintypes.FILETIME()
        ok = self._kernel32.GetProcessTimes(
            wintypes.HANDLE(process_handle),
            ctypes.byref(creation),
            ctypes.byref(exit_t),
            ctypes.byref(kernel_t),
            ctypes.byref(user_t),
        )
        if not ok:
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        return int(
            self._kernel32.WaitForSingleObject(wintypes.HANDLE(handle), timeout_ms)
        )

    def get_exit_code_process(self, process_handle: int) -> tuple[bool, int]:
        code = wintypes.DWORD(0)
        ok = bool(
            self._kernel32.GetExitCodeProcess(
                wintypes.HANDLE(process_handle), ctypes.byref(code)
            )
        )
        return ok, int(code.value)

    def close_handle(self, handle: int) -> bool:
        return bool(self._kernel32.CloseHandle(wintypes.HANDLE(handle)))


# ---------------------------------------------------------------------------
# Managed Process & Result
# ---------------------------------------------------------------------------

class ManagedJobProcess:
    """Represents a process launched and isolated inside a Windows Job Object."""

    def __init__(self, api: Win32Api, identity: ProcessIdentity) -> None:
        self._api = api
        self.identity = identity
        self._closed = False

    @property
    def pid(self) -> int:
        return self.identity.pid

    @property
    def state(self) -> LauncherState:
        return self.identity.state

    @property
    def is_closed(self) -> bool:
        return self._closed

    def verify_identity(self) -> bool:
        """Verify that the process handle still refers to the original process."""
        if self._closed or not self.identity.process_handle:
            return False
        if self.identity.process_creation_time is None:
            return False
        current_ct = self._api.get_process_creation_time(
            self.identity.process_handle
        )
        if current_ct is None:
            return False
        return current_ct == self.identity.process_creation_time

    def query_basic_accounting(self) -> tuple[Optional[BasicAccountingInfo], Optional[JobObjectError]]:
        if self._closed or not self.identity.job_handle:
            return None, JobObjectError(
                stage="query_basic_accounting",
                code="JOB_HANDLE_CLOSED",
                message="Job handle is closed or unavailable.",
            )
        ok, info, err = self._api.query_basic_accounting(self.identity.job_handle)
        if not ok or info is None:
            return None, JobObjectError(
                stage="query_basic_accounting",
                code="QUERY_FAILED",
                message="QueryInformationJobObject(BasicAccountingInformation) failed.",
                winerror=err,
            )
        return (
            BasicAccountingInfo(
                active_processes=int(info.ActiveProcesses),
                total_processes=int(info.TotalProcesses),
                total_terminated_processes=int(info.TotalTerminatedProcesses),
            ),
            None,
        )

    def query_process_id_list(
        self, capacity: int = 64, max_retries: int = 3
    ) -> ProcessIdListResult:
        if self._closed or not self.identity.job_handle:
            return ProcessIdListResult(
                ok=False,
                truncated=False,
                error_message="Job handle is closed or unavailable.",
            )

        current_capacity = max(1, capacity)
        attempts = 0
        last_pids: List[int] = []
        last_assigned = 0
        last_returned = 0

        while attempts <= max_retries:
            attempts += 1
            ok, info, err = self._api.query_process_id_list(
                self.identity.job_handle, capacity=current_capacity
            )
            if not ok:
                if err == ERROR_MORE_DATA:
                    current_capacity *= 2
                    continue
                return ProcessIdListResult(
                    ok=False,
                    truncated=False,
                    error_code=err,
                    error_message=f"QueryInformationJobObject failed with error {err}.",
                )

            assigned = int(info.NumberOfAssignedProcesses)
            returned = int(info.NumberOfProcessIdsInList)
            pids = [int(info.ProcessIdList[i]) for i in range(returned)]
            last_pids = pids
            last_assigned = assigned
            last_returned = returned

            if assigned > returned:
                current_capacity = max(assigned, current_capacity * 2)
                continue

            return ProcessIdListResult(
                ok=True,
                pids=pids,
                assigned_count=assigned,
                returned_count=returned,
                truncated=False,
            )

        return ProcessIdListResult(
            ok=False,
            pids=last_pids,
            assigned_count=last_assigned,
            returned_count=last_returned,
            truncated=True,
            error_code=ERROR_MORE_DATA if last_assigned == 0 else None,
            error_message=(
                f"Truncated PID list after {attempts} attempts: "
                f"assigned={last_assigned}, returned={last_returned}."
            ),
        )

    def terminate_job(
        self,
        timeout_ms: int = 5000,
        poll_interval_ms: int = 50,
        exit_code: int = 1,
    ) -> TerminationResult:
        """Terminate the entire Job Object and independently verify termination."""
        if self._closed or not self.identity.job_handle:
            return TerminationResult(
                status=TerminationStatus.INCONCLUSIVE,
                reason="Job handle is closed or unavailable.",
                error=JobObjectError(
                    stage="terminate_job",
                    code="JOB_HANDLE_CLOSED",
                    message="Cannot terminate: job handle is closed.",
                ),
            )

        try:
            ok = self._api.terminate_job_object(
                self.identity.job_handle, exit_code=exit_code
            )
            if not ok:
                err = self._api.get_last_error()
                return TerminationResult(
                    status=TerminationStatus.INCONCLUSIVE,
                    reason="TerminateJobObject failed.",
                    error=JobObjectError(
                        stage="terminate_job",
                        code="TERMINATE_JOB_FAILED",
                        message="TerminateJobObject returned False.",
                        winerror=err,
                    ),
                )

            deadline = time.monotonic() + (timeout_ms / 1000.0)
            last_acct: Optional[BasicAccountingInfo] = None
            last_pids: Optional[ProcessIdListResult] = None

            while True:
                acct, acct_err = self.query_basic_accounting()
                if acct_err is not None or acct is None:
                    return TerminationResult(
                        status=TerminationStatus.INCONCLUSIVE,
                        reason="Failed to query BasicAccountingInformation after TerminateJobObject.",
                        error=acct_err,
                    )
                last_acct = acct

                pid_res = self.query_process_id_list()
                last_pids = pid_res
                if pid_res.truncated or not pid_res.ok:
                    return TerminationResult(
                        status=TerminationStatus.INCONCLUSIVE,
                        active_processes=acct.active_processes,
                        pids=pid_res.pids,
                        truncated=pid_res.truncated,
                        reason=pid_res.error_message or "PID list query failed or truncated.",
                    )

                if acct.active_processes == 0 and len(pid_res.pids) == 0:
                    self.identity.state = LauncherState.TERMINATED
                    return TerminationResult(
                        status=TerminationStatus.TREE_TERMINATED,
                        active_processes=0,
                        pids=[],
                        truncated=False,
                        reason="Verified ActiveProcesses == 0 and empty PID list.",
                    )

                if time.monotonic() >= deadline:
                    return TerminationResult(
                        status=TerminationStatus.INCONCLUSIVE,
                        active_processes=last_acct.active_processes if last_acct else None,
                        pids=last_pids.pids if last_pids else None,
                        truncated=last_pids.truncated if last_pids else False,
                        reason="Timeout waiting for Job Object processes to terminate.",
                    )

                time.sleep(poll_interval_ms / 1000.0)
        except Exception as exc:
            return TerminationResult(
                status=TerminationStatus.INCONCLUSIVE,
                reason=f"Unexpected exception during termination: {exc}",
                error=JobObjectError(
                    stage="terminate_job",
                    code="UNEXPECTED_EXCEPTION",
                    message=str(exc),
                ),
            )

    def close(self) -> None:
        """Close all open handles safely without double-close."""
        if self._closed:
            return
        first_exc: Optional[BaseException] = None
        for attr in ("thread_handle", "process_handle", "job_handle"):
            handle = getattr(self.identity, attr, None)
            if handle:
                try:
                    self._api.close_handle(handle)
                except Exception as exc:
                    # Preserve handle reference on failure; Win32 CloseHandle
                    # failure leaves handle state indeterminate.
                    if first_exc is None:
                        first_exc = exc
                else:
                    setattr(self.identity, attr, None)
        if first_exc is not None:
            raise first_exc
        self._closed = True

    def __enter__(self) -> ManagedJobProcess:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


@dataclass(frozen=True)
class LaunchResult:
    ok: bool
    process: Optional[ManagedJobProcess] = None
    error: Optional[JobObjectError] = None


# ---------------------------------------------------------------------------
# JobObjectProcessManager
# ---------------------------------------------------------------------------

class JobObjectProcessManager:
    """Launches and manages Windows processes inside isolated Job Objects."""

    def __init__(self, api: Optional[Win32Api] = None) -> None:
        self._api = api if api is not None else Win32Api()

    @staticmethod
    def build_limit_flags() -> int:
        """Return the enforced Job Object limit flags (no breakaway allowed)."""
        flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        assert (flags & FORBIDDEN_BREAKAWAY_FLAGS) == 0
        return flags

    def _safe_abort(
        self,
        job_handle: Optional[int],
        process_handle: Optional[int],
        thread_handle: Optional[int],
    ) -> bool:
        """Terminate suspended process if created, confirm exit, and close all handles."""
        aborted_cleanly = True
        try:
            if process_handle:
                try:
                    terminated = self._api.terminate_process(process_handle, 1)
                    wait_timeout = 3000 if terminated else 0
                    wait_res = self._api.wait_for_single_object(
                        process_handle, wait_timeout
                    )
                    if wait_res != WAIT_OBJECT_0:
                        aborted_cleanly = False
                except Exception:
                    aborted_cleanly = False
        finally:
            for h in (thread_handle, process_handle, job_handle):
                if h:
                    try:
                        if not self._api.close_handle(h):
                            aborted_cleanly = False
                    except Exception:
                        aborted_cleanly = False
        return aborted_cleanly

    def launch(
        self,
        command_line: str,
        working_dir: Optional[str] = None,
        creation_flags: int = 0,
        process_name: Optional[str] = None,
    ) -> LaunchResult:
        """Execute the mandatory 8-step secure Job Object launch sequence."""
        if creation_flags & CREATE_BREAKAWAY_FROM_JOB:
            raise ValueError("CREATE_BREAKAWAY_FROM_JOB is forbidden in creation_flags.")

        job_handle: Optional[int] = None
        process_handle: Optional[int] = None
        thread_handle: Optional[int] = None
        stage = "create_job"

        try:
            # 1. Create Job Object
            job_handle = self._api.create_job_object(None)
            if not job_handle:
                err = self._api.get_last_error()
                return LaunchResult(
                    ok=False,
                    error=JobObjectError(
                        stage=stage,
                        code="CREATE_JOB_FAILED",
                        message="CreateJobObjectW failed.",
                        winerror=err,
                        aborted_cleanly=True,
                    ),
                )

            # 2. Configure Job Object (KILL_ON_JOB_CLOSE, no breakaway)
            stage = "configure_job"
            limit_info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            limit_info.BasicLimitInformation.LimitFlags = self.build_limit_flags()
            if not self._api.set_information_job_object(
                job_handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, limit_info
            ):
                err = self._api.get_last_error()
                clean = self._safe_abort(job_handle, None, None)
                return LaunchResult(
                    ok=False,
                    error=JobObjectError(
                        stage=stage,
                        code="SET_JOB_LIMITS_FAILED",
                        message="SetInformationJobObject failed.",
                        winerror=err,
                        aborted_cleanly=clean,
                    ),
                )

            # 3. Create process with CREATE_SUSPENDED
            stage = "create_process_suspended"
            proc_info = self._api.create_process_suspended(
                command_line=command_line,
                working_dir=working_dir,
                creation_flags=creation_flags,
            )
            if proc_info is None:
                err = self._api.get_last_error()
                clean = self._safe_abort(job_handle, None, None)
                return LaunchResult(
                    ok=False,
                    error=JobObjectError(
                        stage=stage,
                        code="CREATE_PROCESS_FAILED",
                        message="CreateProcessW(CREATE_SUSPENDED) failed.",
                        winerror=err,
                        aborted_cleanly=clean,
                    ),
                )

            process_handle = int(proc_info.hProcess)
            thread_handle = int(proc_info.hThread)
            pid = int(proc_info.dwProcessId)

            # 4. Associate process to Job Object
            stage = "assign_process_to_job"
            if not self._api.assign_process_to_job_object(
                job_handle, process_handle
            ):
                err = self._api.get_last_error()
                clean = self._safe_abort(job_handle, process_handle, thread_handle)
                return LaunchResult(
                    ok=False,
                    error=JobObjectError(
                        stage=stage,
                        code="ASSIGN_JOB_FAILED",
                        message="AssignProcessToJobObject failed; aborting without fallback.",
                        winerror=err,
                        aborted_cleanly=clean,
                    ),
                )

            # 5. Validate association using IsProcessInJob
            stage = "validate_is_process_in_job"
            ok_check, in_job = self._api.is_process_in_job(
                process_handle, job_handle
            )
            if not ok_check or not in_job:
                err = self._api.get_last_error() if not ok_check else None
                clean = self._safe_abort(job_handle, process_handle, thread_handle)
                return LaunchResult(
                    ok=False,
                    error=JobObjectError(
                        stage=stage,
                        code="PROCESS_NOT_IN_JOB",
                        message="IsProcessInJob failed or returned False.",
                        winerror=err,
                        aborted_cleanly=clean,
                    ),
                )

            # 6. Confirm process identity
            stage = "confirm_identity"
            creation_time = self._api.get_process_creation_time(process_handle)
            if creation_time is None:
                err = self._api.get_last_error()
                clean = self._safe_abort(job_handle, process_handle, thread_handle)
                return LaunchResult(
                    ok=False,
                    error=JobObjectError(
                        stage=stage,
                        code="IDENTITY_CONFIRMATION_FAILED",
                        message="GetProcessTimes failed while confirming process identity.",
                        winerror=err,
                        aborted_cleanly=clean,
                    ),
                )

            # 7. Resume thread
            stage = "resume_thread"
            suspend_count = self._api.resume_thread(thread_handle)
            if suspend_count == 0xFFFFFFFF or suspend_count < 0:
                err = self._api.get_last_error()
                clean = self._safe_abort(job_handle, process_handle, thread_handle)
                return LaunchResult(
                    ok=False,
                    error=JobObjectError(
                        stage=stage,
                        code="RESUME_THREAD_FAILED",
                        message="ResumeThread failed.",
                        winerror=err,
                        aborted_cleanly=clean,
                    ),
                )

            # 8. Return managed object
            inferred_name = process_name or os.path.basename(
                command_line.strip().split()[0].strip('"')
            )
            identity = ProcessIdentity(
                pid=pid,
                process_handle=process_handle,
                thread_handle=thread_handle,
                process_creation_time=creation_time,
                job_handle=job_handle,
                process_name=inferred_name,
                command_line=command_line,
                created_at=time.time(),
                state=LauncherState.RUNNING,
            )
            return LaunchResult(
                ok=True,
                process=ManagedJobProcess(self._api, identity),
            )

        except Exception as exc:
            clean = self._safe_abort(job_handle, process_handle, thread_handle)
            return LaunchResult(
                ok=False,
                error=JobObjectError(
                    stage=stage,
                    code="UNEXPECTED_EXCEPTION",
                    message=str(exc),
                    aborted_cleanly=clean,
                ),
            )
