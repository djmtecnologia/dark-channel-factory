"""Windows Job Object process management for isolated watchdog execution."""

from desktop_worker.watchdog.job_object import (
    CREATE_SUSPENDED,
    ERROR_MORE_DATA,
    JOB_OBJECT_LIMIT_BREAKAWAY_OK,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
    BasicAccountingInfo,
    JobObjectError,
    JobObjectProcessManager,
    LaunchResult,
    LauncherState,
    ManagedJobProcess,
    ProcessIdListResult,
    ProcessIdentity,
    TerminationResult,
    TerminationStatus,
    Win32Api,
)

__all__ = [
    "CREATE_SUSPENDED",
    "ERROR_MORE_DATA",
    "JOB_OBJECT_LIMIT_BREAKAWAY_OK",
    "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
    "JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK",
    "BasicAccountingInfo",
    "JobObjectError",
    "JobObjectProcessManager",
    "LaunchResult",
    "LauncherState",
    "ManagedJobProcess",
    "ProcessIdListResult",
    "ProcessIdentity",
    "TerminationResult",
    "TerminationStatus",
    "Win32Api",
]
