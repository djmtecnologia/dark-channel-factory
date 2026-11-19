"""Unit tests for Windows Job Object Process Manager (mocked Win32 API)."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest

from desktop_worker.watchdog.job_object import (
    CREATE_BREAKAWAY_FROM_JOB,
    CREATE_SUSPENDED,
    ERROR_ACCESS_DENIED,
    ERROR_MORE_DATA,
    FORBIDDEN_BREAKAWAY_FLAGS,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    WAIT_FAILED,
    WAIT_OBJECT_0,
    WAIT_TIMEOUT,
    JobObjectProcessManager,
    LauncherState,
    TerminationStatus,
    Win32Api,
)


class FakeWin32Api:
    """Mock Win32Api recording call order and supporting fault injection."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.closed_handles: List[int] = []
        self.last_limit_flags: Optional[int] = None
        self.last_error: int = 0

        # Configurable behaviors
        self.create_job_ok = True
        self.set_info_ok = True
        self.create_proc_ok = True
        self.assign_ok = True
        self.is_in_job_ok = True
        self.is_in_job_value = True
        self.creation_time: Optional[int] = 123456789
        self.resume_result = 1
        self.terminate_proc_ok = True
        self.wait_result = WAIT_OBJECT_0
        self.terminate_job_ok = True

        # Query behaviors
        self.acct_ok = True
        self.acct_active = 0
        self.acct_total = 1
        self.acct_terminated = 1
        self.acct_err = 0

        self.pid_list_ok = True
        self.pid_list_err = 0
        self.pid_assigned = 0
        self.pid_returned = 0
        self.pid_values: List[int] = []

        # Exception injection
        self.raise_on: Optional[str] = None

    def _maybe_raise(self, name: str) -> None:
        if self.raise_on == name:
            raise RuntimeError(f"Injected exception in {name}")

    def get_last_error(self) -> int:
        return self.last_error

    def create_job_object(self, name: Optional[str] = None) -> Optional[int]:
        self.calls.append("create_job_object")
        self._maybe_raise("create_job_object")
        if not self.create_job_ok:
            self.last_error = 5
            return None
        return 1001

    def set_information_job_object(
        self, job_handle: int, info_class: int, info_struct: Any
    ) -> bool:
        self.calls.append("set_information_job_object")
        self.last_limit_flags = int(info_struct.BasicLimitInformation.LimitFlags)
        self._maybe_raise("set_information_job_object")
        if not self.set_info_ok:
            self.last_error = 87
            return False
        return True

    def create_process_suspended(
        self,
        command_line: str,
        working_dir: Optional[str] = None,
        creation_flags: int = 0,
    ) -> Optional[Any]:
        self.calls.append("create_process_suspended")
        self._maybe_raise("create_process_suspended")
        if not self.create_proc_ok:
            self.last_error = 2
            return None
        return SimpleNamespace(
            hProcess=2001,
            hThread=3001,
            dwProcessId=4242,
            dwThreadId=4243,
        )

    def assign_process_to_job_object(
        self, job_handle: int, process_handle: int
    ) -> bool:
        self.calls.append("assign_process_to_job_object")
        self._maybe_raise("assign_process_to_job_object")
        if not self.assign_ok:
            self.last_error = ERROR_ACCESS_DENIED
            return False
        return True

    def is_process_in_job(
        self, process_handle: int, job_handle: int
    ) -> tuple[bool, bool]:
        self.calls.append("is_process_in_job")
        self._maybe_raise("is_process_in_job")
        if not self.is_in_job_ok:
            self.last_error = 6
            return False, False
        return True, self.is_in_job_value

    def get_process_creation_time(self, process_handle: int) -> Optional[int]:
        self.calls.append("get_process_creation_time")
        self._maybe_raise("get_process_creation_time")
        if self.creation_time is None:
            self.last_error = 6
        return self.creation_time

    def resume_thread(self, thread_handle: int) -> int:
        self.calls.append("resume_thread")
        self._maybe_raise("resume_thread")
        if self.resume_result == 0xFFFFFFFF:
            self.last_error = 6
        return self.resume_result

    def terminate_process(self, process_handle: int, exit_code: int = 1) -> bool:
        self.calls.append("terminate_process")
        self._maybe_raise("terminate_process")
        return self.terminate_proc_ok

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        self.calls.append("wait_for_single_object")
        return self.wait_result

    def terminate_job_object(self, job_handle: int, exit_code: int = 1) -> bool:
        self.calls.append("terminate_job_object")
        self._maybe_raise("terminate_job_object")
        if not self.terminate_job_ok:
            self.last_error = 5
            return False
        return True

    def query_basic_accounting(
        self, job_handle: int
    ) -> tuple[bool, Optional[Any], int]:
        self.calls.append("query_basic_accounting")
        self._maybe_raise("query_basic_accounting")
        if not self.acct_ok:
            return False, None, self.acct_err
        return (
            True,
            SimpleNamespace(
                ActiveProcesses=self.acct_active,
                TotalProcesses=self.acct_total,
                TotalTerminatedProcesses=self.acct_terminated,
            ),
            0,
        )

    def query_process_id_list(
        self, job_handle: int, capacity: int = 64
    ) -> tuple[bool, Any, int]:
        self.calls.append("query_process_id_list")
        self._maybe_raise("query_process_id_list")
        if not self.pid_list_ok:
            return False, None, self.pid_list_err
        return (
            True,
            SimpleNamespace(
                NumberOfAssignedProcesses=self.pid_assigned,
                NumberOfProcessIdsInList=self.pid_returned,
                ProcessIdList=self.pid_values,
            ),
            0,
        )

    def close_handle(self, handle: int) -> bool:
        self.calls.append(f"close_handle({handle})")
        self.closed_handles.append(handle)
        return True


def test_1_successful_launch_and_order():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe -c pass")

    assert res.ok is True
    assert res.process is not None
    assert res.process.pid == 4242
    assert res.process.state == LauncherState.RUNNING
    assert api.calls == [
        "create_job_object",
        "set_information_job_object",
        "create_process_suspended",
        "assign_process_to_job_object",
        "is_process_in_job",
        "get_process_creation_time",
        "resume_thread",
    ]
    res.process.close()
    assert sorted(api.closed_handles) == [1001, 2001, 3001]


def test_2_create_job_object_failure():
    api = FakeWin32Api()
    api.create_job_ok = False
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error is not None
    assert res.error.code == "CREATE_JOB_FAILED"
    assert "resume_thread" not in api.calls


def test_3_set_information_job_object_failure():
    api = FakeWin32Api()
    api.set_info_ok = False
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error.code == "SET_JOB_LIMITS_FAILED"
    assert api.closed_handles == [1001]
    assert "create_process_suspended" not in api.calls


def test_4_create_process_w_failure():
    api = FakeWin32Api()
    api.create_proc_ok = False
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error.code == "CREATE_PROCESS_FAILED"
    assert api.closed_handles == [1001]


def test_5_assign_process_to_job_failure_aborts_without_resume():
    api = FakeWin32Api()
    api.assign_ok = False
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error.code == "ASSIGN_JOB_FAILED"
    assert res.error.winerror == ERROR_ACCESS_DENIED
    assert "resume_thread" not in api.calls
    assert "terminate_process" in api.calls
    assert sorted(api.closed_handles) == [1001, 2001, 3001]


def test_6_is_process_in_job_api_failure():
    api = FakeWin32Api()
    api.is_in_job_ok = False
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error.code == "PROCESS_NOT_IN_JOB"
    assert "resume_thread" not in api.calls
    assert "terminate_process" in api.calls
    assert sorted(api.closed_handles) == [1001, 2001, 3001]


def test_7_process_not_associated():
    api = FakeWin32Api()
    api.is_in_job_value = False
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error.code == "PROCESS_NOT_IN_JOB"
    assert "resume_thread" not in api.calls
    assert "terminate_process" in api.calls
    assert sorted(api.closed_handles) == [1001, 2001, 3001]


def test_8_resume_thread_failure():
    api = FakeWin32Api()
    api.resume_result = 0xFFFFFFFF
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error.code == "RESUME_THREAD_FAILED"
    assert "terminate_process" in api.calls
    assert sorted(api.closed_handles) == [1001, 2001, 3001]


def test_9_safe_abort_on_identity_failure():
    api = FakeWin32Api()
    api.creation_time = None
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error.code == "IDENTITY_CONFIRMATION_FAILED"
    assert res.error.aborted_cleanly is True
    assert "resume_thread" not in api.calls
    assert "terminate_process" in api.calls
    assert "wait_for_single_object" in api.calls


def test_10_terminate_process_failure_during_abort():
    api = FakeWin32Api()
    api.assign_ok = False
    api.terminate_proc_ok = False
    api.wait_result = WAIT_TIMEOUT
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error.aborted_cleanly is False
    assert sorted(api.closed_handles) == [1001, 2001, 3001]


def test_11_terminate_job_object_failure():
    api = FakeWin32Api()
    api.terminate_job_ok = False
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None

    term = res.process.terminate_job(timeout_ms=100)
    assert term.status == TerminationStatus.INCONCLUSIVE
    assert term.error is not None
    assert term.error.code == "TERMINATE_JOB_FAILED"
    res.process.close()


def test_12_error_more_data_detected():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None

    api.pid_list_ok = False
    api.pid_list_err = ERROR_MORE_DATA
    pid_res = res.process.query_process_id_list()
    assert pid_res.ok is False
    assert pid_res.truncated is True
    assert pid_res.error_code == ERROR_MORE_DATA

    term = res.process.terminate_job(timeout_ms=50)
    assert term.status == TerminationStatus.INCONCLUSIVE
    assert term.truncated is True
    res.process.close()


def test_13_truncated_pid_list_detected():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None

    api.pid_assigned = 5
    api.pid_returned = 2
    api.pid_values = [10, 20]
    pid_res = res.process.query_process_id_list()
    assert pid_res.ok is False
    assert pid_res.truncated is True
    assert pid_res.pids == [10, 20]

    term = res.process.terminate_job(timeout_ms=50)
    assert term.status == TerminationStatus.INCONCLUSIVE
    assert term.truncated is True
    res.process.close()


def test_14_active_processes_gt_zero_returns_inconclusive():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None

    api.acct_active = 1
    api.pid_assigned = 1
    api.pid_returned = 1
    api.pid_values = [4242]
    term = res.process.terminate_job(timeout_ms=60, poll_interval_ms=20)
    assert term.status == TerminationStatus.INCONCLUSIVE
    assert term.active_processes == 1
    res.process.close()


def test_15_handles_closed_and_no_double_close():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    with mgr.launch("python.exe").process as proc:  # type: ignore[union-attr]
        assert proc.is_closed is False
    assert proc.is_closed is True
    assert sorted(api.closed_handles) == [1001, 2001, 3001]

    # Second close must be a no-op
    proc.close()
    assert sorted(api.closed_handles) == [1001, 2001, 3001]


def test_16_no_breakaway_flags_enabled():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None
    assert api.last_limit_flags == JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert (api.last_limit_flags & FORBIDDEN_BREAKAWAY_FLAGS) == 0
    res.process.close()


def test_17_pid_reuse_detected_by_identity_check():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None
    assert res.process.verify_identity() is True

    # Simulate different creation time (e.g. PID reused by another process)
    api.creation_time = 999999999
    assert res.process.verify_identity() is False
    res.process.close()


@pytest.mark.parametrize(
    "failing_call",
    [
        "create_job_object",
        "set_information_job_object",
        "create_process_suspended",
        "assign_process_to_job_object",
        "is_process_in_job",
        "get_process_creation_time",
        "resume_thread",
    ],
)
def test_18_unexpected_exception_in_critical_steps(failing_call: str):
    api = FakeWin32Api()
    api.raise_on = failing_call
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error is not None
    assert res.error.code == "UNEXPECTED_EXCEPTION"
    # No leaked handles
    if failing_call == "create_job_object":
        assert api.closed_handles == []
    elif failing_call in ("set_information_job_object", "create_process_suspended"):
        assert api.closed_handles == [1001]
    else:
        assert sorted(api.closed_handles) == [1001, 2001, 3001]


# ---------------------------------------------------------------------------
# Audit Additions: Safe Abort, PID List Resizing, Handle Management
# ---------------------------------------------------------------------------

def test_audit_abort_wait_timeout_returns_aborted_cleanly_false():
    api = FakeWin32Api()
    api.assign_ok = False
    api.wait_result = WAIT_TIMEOUT
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error is not None
    assert res.error.aborted_cleanly is False
    assert sorted(api.closed_handles) == [1001, 2001, 3001]


def test_audit_abort_wait_failed_returns_aborted_cleanly_false():
    api = FakeWin32Api()
    api.assign_ok = False
    api.wait_result = WAIT_FAILED
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error is not None
    assert res.error.aborted_cleanly is False
    assert sorted(api.closed_handles) == [1001, 2001, 3001]


def test_audit_abort_terminate_fails_and_process_still_alive():
    api = FakeWin32Api()
    api.assign_ok = False
    api.terminate_proc_ok = False
    api.wait_result = WAIT_TIMEOUT
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error is not None
    assert res.error.aborted_cleanly is False


def test_audit_abort_terminate_fails_but_process_already_exited():
    api = FakeWin32Api()
    api.assign_ok = False
    api.terminate_proc_ok = False
    api.wait_result = WAIT_OBJECT_0
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error is not None
    assert res.error.aborted_cleanly is True
    assert sorted(api.closed_handles) == [1001, 2001, 3001]


def test_audit_pid_list_resizes_on_error_more_data_and_succeeds():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None

    capacities_seen: List[int] = []

    def dynamic_query(job_handle: int, capacity: int = 64):
        capacities_seen.append(capacity)
        if capacity < 4:
            return False, None, ERROR_MORE_DATA
        return (
            True,
            SimpleNamespace(
                NumberOfAssignedProcesses=3,
                NumberOfProcessIdsInList=3,
                ProcessIdList=[101, 102, 103],
            ),
            0,
        )

    api.query_process_id_list = dynamic_query  # type: ignore[method-assign]
    pid_res = res.process.query_process_id_list(capacity=2, max_retries=3)
    assert pid_res.ok is True
    assert pid_res.truncated is False
    assert pid_res.pids == [101, 102, 103]
    assert capacities_seen == [2, 4]
    res.process.close()


def test_audit_pid_list_resizes_when_assigned_gt_returned():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None

    capacities_seen: List[int] = []

    def dynamic_query(job_handle: int, capacity: int = 64):
        capacities_seen.append(capacity)
        if capacity < 5:
            return (
                True,
                SimpleNamespace(
                    NumberOfAssignedProcesses=5,
                    NumberOfProcessIdsInList=2,
                    ProcessIdList=[101, 102],
                ),
                0,
            )
        return (
            True,
            SimpleNamespace(
                NumberOfAssignedProcesses=5,
                NumberOfProcessIdsInList=5,
                ProcessIdList=[101, 102, 103, 104, 105],
            ),
            0,
        )

    api.query_process_id_list = dynamic_query  # type: ignore[method-assign]
    pid_res = res.process.query_process_id_list(capacity=2, max_retries=3)
    assert pid_res.ok is True
    assert pid_res.truncated is False
    assert pid_res.pids == [101, 102, 103, 104, 105]
    assert capacities_seen == [2, 5]
    res.process.close()


def test_audit_pid_list_exhausts_retries_on_growing_processes():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None

    call_count = 0

    def growing_query(job_handle: int, capacity: int = 64):
        nonlocal call_count
        call_count += 1
        return (
            True,
            SimpleNamespace(
                NumberOfAssignedProcesses=capacity + 10,
                NumberOfProcessIdsInList=capacity,
                ProcessIdList=list(range(1, capacity + 1)),
            ),
            0,
        )

    api.query_process_id_list = growing_query  # type: ignore[method-assign]
    pid_res = res.process.query_process_id_list(capacity=2, max_retries=2)
    assert call_count == 3
    assert pid_res.ok is False
    assert pid_res.truncated is True
    assert len(pid_res.pids) > 0
    res.process.close()


def test_audit_handle_close_failure_and_exception_still_closes_remaining():
    api = FakeWin32Api()
    api.assign_ok = False

    def flaky_close(handle: int) -> bool:
        api.closed_handles.append(handle)
        if handle == 3001:
            raise OSError("CloseHandle failed")
        if handle == 2001:
            return False
        return True

    api.close_handle = flaky_close  # type: ignore[method-assign]
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")

    assert res.ok is False
    assert res.error is not None
    assert res.error.aborted_cleanly is False
    assert api.closed_handles == [3001, 2001, 1001]


def test_audit_handle_close_order_and_idempotency():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None

    res.process.close()
    assert api.closed_handles == [3001, 2001, 1001]
    res.process.close()
    assert api.closed_handles == [3001, 2001, 1001]


def test_close_normal_all_handles_and_idempotency():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None
    proc = res.process

    proc.close()

    assert api.closed_handles == [3001, 2001, 1001]
    assert proc.identity.thread_handle is None
    assert proc.identity.process_handle is None
    assert proc.identity.job_handle is None
    assert proc._closed is True

    proc.close()
    assert api.closed_handles == [3001, 2001, 1001]


def test_close_failure_on_first_handle_preserves_ref_and_closes_remaining():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None
    proc = res.process

    def flaky_close(handle: int) -> bool:
        api.closed_handles.append(handle)
        if handle == 3001:
            raise OSError("CloseHandle failed on thread_handle")
        return True

    api.close_handle = flaky_close  # type: ignore[method-assign]

    with pytest.raises(OSError, match="thread_handle"):
        proc.close()

    assert api.closed_handles == [3001, 2001, 1001]
    assert proc.identity.thread_handle == 3001
    assert proc.identity.process_handle is None
    assert proc.identity.job_handle is None
    assert proc._closed is False


def test_close_retry_after_failure_closes_only_pending_handle():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None
    proc = res.process

    should_fail = True

    def flaky_close(handle: int) -> bool:
        api.closed_handles.append(handle)
        if handle == 3001 and should_fail:
            raise OSError("CloseHandle failed on thread_handle")
        return True

    api.close_handle = flaky_close  # type: ignore[method-assign]

    with pytest.raises(OSError, match="thread_handle"):
        proc.close()

    assert api.closed_handles == [3001, 2001, 1001]
    assert proc._closed is False

    should_fail = False
    proc.close()

    assert api.closed_handles == [3001, 2001, 1001, 3001]
    assert proc.identity.thread_handle is None
    assert proc.identity.process_handle is None
    assert proc.identity.job_handle is None
    assert proc._closed is True


def test_close_multiple_failures_preserves_failed_refs_and_raises_first():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None
    proc = res.process

    def flaky_close(handle: int) -> bool:
        api.closed_handles.append(handle)
        if handle == 3001:
            raise OSError("first failure: thread_handle")
        if handle == 2001:
            raise RuntimeError("second failure: process_handle")
        return True

    api.close_handle = flaky_close  # type: ignore[method-assign]

    with pytest.raises(OSError, match="first failure: thread_handle"):
        proc.close()

    assert api.closed_handles == [3001, 2001, 1001]
    assert proc.identity.thread_handle == 3001
    assert proc.identity.process_handle == 2001
    assert proc.identity.job_handle is None
    assert proc._closed is False


def test_close_with_no_handles_is_idempotent():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe")
    assert res.ok and res.process is not None
    proc = res.process

    proc.identity.thread_handle = None
    proc.identity.process_handle = None
    proc.identity.job_handle = None

    proc.close()
    assert proc._closed is True
    assert api.closed_handles == []


# ---------------------------------------------------------------------------
# Process Creation Flags Tests (Sprint 2.7 Task 1.4)
# ---------------------------------------------------------------------------

_CREATE_NO_WINDOW = 0x08000000


def test_creation_flags_breakaway_rejected_in_launch():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="CREATE_BREAKAWAY_FROM_JOB"):
        mgr.launch("python.exe -c pass", creation_flags=CREATE_BREAKAWAY_FROM_JOB)

    assert api.calls == []
    assert api.closed_handles == []


def test_creation_flags_breakaway_rejected_in_win32_wrapper():
    mock_kernel32 = MagicMock()
    api = Win32Api()
    api._kernel32 = mock_kernel32

    with pytest.raises(ValueError, match="CREATE_BREAKAWAY_FROM_JOB"):
        api.create_process_suspended(
            "python.exe -c pass",
            creation_flags=CREATE_BREAKAWAY_FROM_JOB,
        )

    mock_kernel32.CreateProcessW.assert_not_called()


def test_creation_flags_create_suspended_enforced_and_sequence():
    mock_kernel32 = MagicMock()
    mock_kernel32.CreateProcessW.return_value = 1
    win32_api = Win32Api()
    win32_api._kernel32 = mock_kernel32

    proc_info = win32_api.create_process_suspended(
        "python.exe -c pass",
        creation_flags=0,
    )
    assert proc_info is not None
    mock_kernel32.CreateProcessW.assert_called_once()
    dw_creation_flags = mock_kernel32.CreateProcessW.call_args[0][5]
    assert dw_creation_flags & CREATE_SUSPENDED == CREATE_SUSPENDED
    assert dw_creation_flags & CREATE_BREAKAWAY_FROM_JOB == 0

    fake_api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=fake_api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe -c pass", creation_flags=0)

    assert res.ok is True
    assert res.process is not None
    assert fake_api.calls == [
        "create_job_object",
        "set_information_job_object",
        "create_process_suspended",
        "assign_process_to_job_object",
        "is_process_in_job",
        "get_process_creation_time",
        "resume_thread",
    ]
    res.process.close()


def test_creation_flags_legitimate_flags_preserved():
    mock_kernel32 = MagicMock()
    mock_kernel32.CreateProcessW.return_value = 1
    win32_api = Win32Api()
    win32_api._kernel32 = mock_kernel32

    proc_info = win32_api.create_process_suspended(
        "python.exe -c pass",
        creation_flags=_CREATE_NO_WINDOW,
    )
    assert proc_info is not None
    mock_kernel32.CreateProcessW.assert_called_once()
    dw_creation_flags = mock_kernel32.CreateProcessW.call_args[0][5]
    assert dw_creation_flags & _CREATE_NO_WINDOW == _CREATE_NO_WINDOW
    assert dw_creation_flags & CREATE_SUSPENDED == CREATE_SUSPENDED
    assert dw_creation_flags & CREATE_BREAKAWAY_FROM_JOB == 0


def test_creation_flags_explicit_validation_not_assert():
    launch_src = inspect.getsource(JobObjectProcessManager.launch)
    wrapper_src = inspect.getsource(Win32Api.create_process_suspended)

    for src in (launch_src, wrapper_src):
        assert "CREATE_BREAKAWAY_FROM_JOB" in src
        assert "raise ValueError" in src
        assert "assert " not in src


def test_terminate_job_confirmed_tree_terminated():
    api = FakeWin32Api()
    api.active_processes = 0
    api.pid_list = []
    mgr = JobObjectProcessManager(api=api)
    launch_res = mgr.launch("python.exe -c pass")
    assert launch_res.ok and launch_res.process is not None

    term_res = launch_res.process.terminate_job(timeout_ms=100, poll_interval_ms=1)

    assert term_res.status == TerminationStatus.TREE_TERMINATED
    assert term_res.active_processes == 0
    assert term_res.pids == []
    assert term_res.truncated is False
    assert launch_res.process.state == LauncherState.TERMINATED


def test_terminate_job_query_basic_accounting_failure_returns_inconclusive():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)
    launch_res = mgr.launch("python.exe -c pass")
    assert launch_res.ok and launch_res.process is not None

    api.query_basic_accounting = lambda job_handle: (False, None, 5)
    term_res = launch_res.process.terminate_job(timeout_ms=50, poll_interval_ms=1)

    assert term_res.status == TerminationStatus.INCONCLUSIVE
    assert term_res.error is not None
    assert term_res.error.code == "QUERY_FAILED"
    assert term_res.error.winerror == 5


def test_terminate_job_query_pid_list_failure_returns_inconclusive():
    api = FakeWin32Api()
    api.active_processes = 0
    mgr = JobObjectProcessManager(api=api)
    launch_res = mgr.launch("python.exe -c pass")
    assert launch_res.ok and launch_res.process is not None

    api.query_process_id_list = lambda job_handle, capacity=64: (False, None, 5)
    term_res = launch_res.process.terminate_job(timeout_ms=50, poll_interval_ms=1)

    assert term_res.status == TerminationStatus.INCONCLUSIVE
    assert term_res.active_processes == 0
    assert "5" in term_res.reason


def test_job_with_no_active_processes_queries_and_terminates():
    api = FakeWin32Api()
    api.active_processes = 0
    api.pid_list = []
    mgr = JobObjectProcessManager(api=api)
    launch_res = mgr.launch("python.exe -c pass")
    assert launch_res.ok and launch_res.process is not None

    acct, acct_err = launch_res.process.query_basic_accounting()
    assert acct_err is None
    assert acct is not None
    assert acct.active_processes == 0

    pid_res = launch_res.process.query_process_id_list()
    assert pid_res.ok is True
    assert pid_res.pids == []
    assert pid_res.truncated is False

    term_res = launch_res.process.terminate_job(timeout_ms=50, poll_interval_ms=1)
    assert term_res.status == TerminationStatus.TREE_TERMINATED



    proc.close()
    assert proc._closed is True
    assert api.closed_handles == []


# ---------------------------------------------------------------------------
# Process Creation Flags Tests (Sprint 2.7 Task 1.4)
# ---------------------------------------------------------------------------

_CREATE_NO_WINDOW = 0x08000000


def test_creation_flags_breakaway_rejected_in_launch():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="CREATE_BREAKAWAY_FROM_JOB"):
        mgr.launch("python.exe -c pass", creation_flags=CREATE_BREAKAWAY_FROM_JOB)

    assert api.calls == []
    assert api.closed_handles == []


def test_creation_flags_breakaway_rejected_in_win32_wrapper():
    mock_kernel32 = MagicMock()
    api = Win32Api()
    api._kernel32 = mock_kernel32

    with pytest.raises(ValueError, match="CREATE_BREAKAWAY_FROM_JOB"):
        api.create_process_suspended(
            "python.exe -c pass",
            creation_flags=CREATE_BREAKAWAY_FROM_JOB,
        )

    mock_kernel32.CreateProcessW.assert_not_called()


def test_creation_flags_create_suspended_enforced_and_sequence():
    mock_kernel32 = MagicMock()
    mock_kernel32.CreateProcessW.return_value = 1
    win32_api = Win32Api()
    win32_api._kernel32 = mock_kernel32

    proc_info = win32_api.create_process_suspended(
        "python.exe -c pass",
        creation_flags=0,
    )
    assert proc_info is not None
    mock_kernel32.CreateProcessW.assert_called_once()
    dw_creation_flags = mock_kernel32.CreateProcessW.call_args[0][5]
    assert dw_creation_flags & CREATE_SUSPENDED == CREATE_SUSPENDED
    assert dw_creation_flags & CREATE_BREAKAWAY_FROM_JOB == 0

    fake_api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=fake_api)  # type: ignore[arg-type]
    res = mgr.launch("python.exe -c pass", creation_flags=0)

    assert res.ok is True
    assert res.process is not None
    assert fake_api.calls == [
        "create_job_object",
        "set_information_job_object",
        "create_process_suspended",
        "assign_process_to_job_object",
        "is_process_in_job",
        "get_process_creation_time",
        "resume_thread",
    ]
    res.process.close()


def test_creation_flags_legitimate_flags_preserved():
    mock_kernel32 = MagicMock()
    mock_kernel32.CreateProcessW.return_value = 1
    win32_api = Win32Api()
    win32_api._kernel32 = mock_kernel32

    proc_info = win32_api.create_process_suspended(
        "python.exe -c pass",
        creation_flags=_CREATE_NO_WINDOW,
    )
    assert proc_info is not None
    mock_kernel32.CreateProcessW.assert_called_once()
    dw_creation_flags = mock_kernel32.CreateProcessW.call_args[0][5]
    assert dw_creation_flags & _CREATE_NO_WINDOW == _CREATE_NO_WINDOW
    assert dw_creation_flags & CREATE_SUSPENDED == CREATE_SUSPENDED
    assert dw_creation_flags & CREATE_BREAKAWAY_FROM_JOB == 0


def test_creation_flags_explicit_validation_not_assert():
    launch_src = inspect.getsource(JobObjectProcessManager.launch)
    wrapper_src = inspect.getsource(Win32Api.create_process_suspended)

    for src in (launch_src, wrapper_src):
        assert "CREATE_BREAKAWAY_FROM_JOB" in src
        assert "raise ValueError" in src
        assert "assert " not in src


def test_terminate_job_confirmed_tree_terminated():
    api = FakeWin32Api()
    api.active_processes = 0
    api.pid_list = []
    mgr = JobObjectProcessManager(api=api)
    launch_res = mgr.launch("python.exe -c pass")
    assert launch_res.ok and launch_res.process is not None

    term_res = launch_res.process.terminate_job(timeout_ms=100, poll_interval_ms=1)

    assert term_res.status == TerminationStatus.TREE_TERMINATED
    assert term_res.active_processes == 0
    assert term_res.pids == []
    assert term_res.truncated is False
    assert launch_res.process.state == LauncherState.TERMINATED


def test_terminate_job_query_basic_accounting_failure_returns_inconclusive():
    api = FakeWin32Api()
    mgr = JobObjectProcessManager(api=api)
    launch_res = mgr.launch("python.exe -c pass")
    assert launch_res.ok and launch_res.process is not None

    api.query_basic_accounting = lambda job_handle: (False, None, 5)
    term_res = launch_res.process.terminate_job(timeout_ms=50, poll_interval_ms=1)

    assert term_res.status == TerminationStatus.INCONCLUSIVE
    assert term_res.error is not None
    assert term_res.error.code == "QUERY_FAILED"
    assert term_res.error.winerror == 5


def test_terminate_job_query_pid_list_failure_returns_inconclusive():
    api = FakeWin32Api()
    api.active_processes = 0
    mgr = JobObjectProcessManager(api=api)
    launch_res = mgr.launch("python.exe -c pass")
    assert launch_res.ok and launch_res.process is not None

    api.query_process_id_list = lambda job_handle, capacity=64: (False, None, 5)
    term_res = launch_res.process.terminate_job(timeout_ms=50, poll_interval_ms=1)

    assert term_res.status == TerminationStatus.INCONCLUSIVE
    assert term_res.active_processes == 0
    assert "5" in term_res.reason


def test_job_with_no_active_processes_queries_and_terminates():
    api = FakeWin32Api()
    api.active_processes = 0
    api.pid_list = []
    mgr = JobObjectProcessManager(api=api)
    launch_res = mgr.launch("python.exe -c pass")
    assert launch_res.ok and launch_res.process is not None

    acct, acct_err = launch_res.process.query_basic_accounting()
    assert acct_err is None
    assert acct is not None
    assert acct.active_processes == 0

    pid_res = launch_res.process.query_process_id_list()
    assert pid_res.ok is True
    assert pid_res.pids == []
    assert pid_res.truncated is False

    term_res = launch_res.process.terminate_job(timeout_ms=50, poll_interval_ms=1)
    assert term_res.status == TerminationStatus.TREE_TERMINATED


