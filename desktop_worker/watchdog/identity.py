"""Execution identity model and anti-PID-reuse validator for Local Watchdog."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union

from desktop_worker.watchdog.job_object import (
    ProcessIdentity,
    Win32Api,
)


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
JOB_OBJECT_QUERY = 0x0004
ERROR_INVALID_PARAMETER = 87
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5


class IdentityStatus(str, Enum):
    """Result of validating a running process against expected execution identity."""

    VALID = "VALID"
    PID_REUSED = "PID_REUSED"
    PROCESS_NOT_FOUND = "PROCESS_NOT_FOUND"
    NOT_IN_JOB = "NOT_IN_JOB"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True)
class CanonicalExecutionIdentity:
    """Canonical 11-field execution identity tuple (Sprint 2.7 / Etapa 0.8).

    Note on ``job_object_name``:
        Because the underlying Win32 Job Object is created anonymously
        (``CreateJobObjectW(None)``), ``job_object_name`` is strictly a
        **logical identifier** retained for the 11-field Etapa 0.8 contract
        and correlation/observability. It is **not** a real Win32 kernel
        object name and has no capability to open (``OpenJobObjectW``) or
        validate a Job Object. ``IdentityValidator`` validates Job Object
        membership exclusively through the real ``job_handle``.
    """

    execution_id: str
    attempt_id: str
    job_id: str
    tenant_id: str
    channel_id: str
    worker_id: str
    fencing_token: int
    pid: int
    process_create_time: int  # Win32 FILETIME (100-ns intervals since 1601-01-01 UTC)
    job_object_name: str  # Logical identifier only; Job Object is anonymous (not a Win32 name)
    browser_user_data_dir: str

    def to_process_identity(
        self, *, job_handle: Optional[int] = None
    ) -> ProcessIdentity:
        """Convert to Task 1 ProcessIdentity."""
        return ProcessIdentity(
            pid=self.pid,
            process_handle=None,
            thread_handle=None,
            process_creation_time=self.process_create_time,
            job_handle=job_handle,
            process_name="",
            command_line="",
            created_at=0.0,
        )


@dataclass(frozen=True)
class IdentityVerificationResult:
    """Outcome of IdentityValidator.verify()."""

    status: IdentityStatus
    pid: int
    expected_create_time: Optional[int]
    actual_create_time: Optional[int] = None
    in_expected_job: Optional[bool] = None
    reason: str = ""

    @property
    def is_valid(self) -> bool:
        """True only when the process is confirmed to be the exact expected execution."""
        return self.status == IdentityStatus.VALID

    @property
    def is_safe_to_target(self) -> bool:
        """True only when targeting this PID will not affect an unrelated process."""
        return self.status == IdentityStatus.VALID


class IdentityValidator:
    """Validates process identity against PID reuse using create_time and Job Object."""

    def __init__(self, win32: Optional[Win32Api] = None) -> None:
        self._win32 = win32 or Win32Api()

    def verify(
        self,
        expected: Union[CanonicalExecutionIdentity, ProcessIdentity],
        *,
        job_handle: Optional[int] = None,
        tolerance_ticks: int = 0,
    ) -> IdentityVerificationResult:
        """Verify that `expected.pid` still belongs to the exact same process and Job Object."""
        if isinstance(expected, CanonicalExecutionIdentity):
            expected_create_time: Optional[int] = expected.process_create_time
            expected_job_handle: Optional[int] = job_handle
        else:
            expected_create_time = expected.process_creation_time
            expected_job_handle = job_handle if job_handle is not None else expected.job_handle

        if expected_create_time is None:
            return IdentityVerificationResult(
                status=IdentityStatus.UNVERIFIABLE,
                pid=expected.pid,
                expected_create_time=None,
                reason="Expected process creation time is missing",
            )

        if expected.pid <= 0:
            return IdentityVerificationResult(
                status=IdentityStatus.PROCESS_NOT_FOUND,
                pid=expected.pid,
                expected_create_time=expected_create_time,
                reason="Invalid or non-positive PID",
            )

        h_process = self._open_process_query(expected.pid)
        if not h_process:
            err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
            if err == ERROR_ACCESS_DENIED:
                return IdentityVerificationResult(
                    status=IdentityStatus.UNVERIFIABLE,
                    pid=expected.pid,
                    expected_create_time=expected_create_time,
                    reason="Access denied opening process for identity check",
                )
            return IdentityVerificationResult(
                status=IdentityStatus.PROCESS_NOT_FOUND,
                pid=expected.pid,
                expected_create_time=expected_create_time,
                reason=f"Process {expected.pid} not found (win32_err={err})",
            )

        try:
            try:
                actual_create_time = self._win32.get_process_creation_time(h_process)
            except Exception as exc:
                return IdentityVerificationResult(
                    status=IdentityStatus.UNVERIFIABLE,
                    pid=expected.pid,
                    expected_create_time=expected_create_time,
                    reason=f"Failed to query process create time: {exc}",
                )

            if actual_create_time is None:
                return IdentityVerificationResult(
                    status=IdentityStatus.UNVERIFIABLE,
                    pid=expected.pid,
                    expected_create_time=expected_create_time,
                    reason="GetProcessTimes returned None",
                )

            if abs(actual_create_time - expected_create_time) > tolerance_ticks:
                return IdentityVerificationResult(
                    status=IdentityStatus.PID_REUSED,
                    pid=expected.pid,
                    expected_create_time=expected_create_time,
                    actual_create_time=actual_create_time,
                    reason=(
                        f"PID {expected.pid} reused: expected create_time="
                        f"{expected_create_time}, actual={actual_create_time}"
                    ),
                )

            in_job: Optional[bool] = None
            if expected_job_handle is not None:
                ok_check, in_job_val = self._win32.is_process_in_job(
                    h_process, int(expected_job_handle)
                )
                in_job = in_job_val if ok_check else None

            if in_job is None:
                return IdentityVerificationResult(
                    status=IdentityStatus.UNVERIFIABLE,
                    pid=expected.pid,
                    expected_create_time=expected_create_time,
                    actual_create_time=actual_create_time,
                    in_expected_job=None,
                    reason="Unable to verify Job Object membership (no valid job handle)",
                )

            if in_job is False:
                return IdentityVerificationResult(
                    status=IdentityStatus.NOT_IN_JOB,
                    pid=expected.pid,
                    expected_create_time=expected_create_time,
                    actual_create_time=actual_create_time,
                    in_expected_job=False,
                    reason=f"Process {expected.pid} is not in expected Job Object",
                )

            return IdentityVerificationResult(
                status=IdentityStatus.VALID,
                pid=expected.pid,
                expected_create_time=expected_create_time,
                actual_create_time=actual_create_time,
                in_expected_job=True,
                reason="Process identity verified",
            )
        finally:
            self._win32.close_handle(h_process)

    def _open_process_query(self, pid: int) -> Optional[int]:
        kernel32 = getattr(self._win32, "_kernel32", None)
        if kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        return int(handle) if handle else None
