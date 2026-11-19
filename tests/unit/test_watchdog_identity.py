"""Unit tests for Task 2: IdentityValidator & ProcessLauncher."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from desktop_worker.watchdog.identity import (
    CanonicalExecutionIdentity,
    IdentityStatus,
    IdentityValidator,
)
from desktop_worker.watchdog.job_object import LauncherState
from desktop_worker.watchdog.process_launcher import (
    AttemptLaunchContext,
    ProcessLauncherError,
    build_upload_environment,
    launch_isolated_upload,
)


def _sample_ctx() -> AttemptLaunchContext:
    return AttemptLaunchContext(
        execution_id="exec-001",
        attempt_id="att-001",
        job_id="job-001",
        tenant_id="tenant-01",
        channel_id="chan-01",
        worker_id="win-worker-01",
        fencing_token=7,
        browser_user_data_dir="C:/tmp/profile_att_001",
    )


def _sample_identity(pid: int = 100, create_time: float = 10.0) -> CanonicalExecutionIdentity:
    return CanonicalExecutionIdentity(
        execution_id="exec-001",
        attempt_id="att-001",
        job_id="job-001",
        tenant_id="tenant-01",
        channel_id="chan-01",
        worker_id="win-worker-01",
        fencing_token=7,
        pid=pid,
        process_create_time=create_time,
        job_object_name="Local\\DCF_UPLOAD_att-001_exec-001",
        browser_user_data_dir="C:/tmp/profile_att_001",
    )


def test_ut01_pid_reused_when_create_time_mismatches() -> None:
    """UT-01: Mismatched process_create_time must return PID_REUSED and never target."""
    win32 = MagicMock()
    win32.get_process_creation_time.return_value = 99.0
    validator = IdentityValidator(win32=win32)
    validator._open_process_query = MagicMock(return_value=1234)  # type: ignore[method-assign]

    res = validator.verify(_sample_identity(pid=100, create_time=10.0))

    assert res.status == IdentityStatus.PID_REUSED
    assert res.is_valid is False
    assert res.is_safe_to_target is False
    assert res.actual_create_time == 99.0
    win32.close_handle.assert_called_once_with(1234)


def test_identity_valid_when_create_time_and_job_match() -> None:
    win32 = MagicMock()
    win32.get_process_creation_time.return_value = 133000000000000000
    win32.is_process_in_job.return_value = (True, True)
    validator = IdentityValidator(win32=win32)
    validator._open_process_query = MagicMock(return_value=1234)  # type: ignore[method-assign]

    res = validator.verify(_sample_identity(pid=100, create_time=133000000000000000), job_handle=999)

    assert res.status == IdentityStatus.VALID
    assert res.is_valid is True
    assert res.is_safe_to_target is True
    assert res.in_expected_job is True


def test_identity_not_in_job_blocks_targeting() -> None:
    win32 = MagicMock()
    win32.get_process_creation_time.return_value = 133000000000000000
    win32.is_process_in_job.return_value = (True, False)
    validator = IdentityValidator(win32=win32)
    validator._open_process_query = MagicMock(return_value=1234)  # type: ignore[method-assign]

    res = validator.verify(_sample_identity(pid=100, create_time=133000000000000000), job_handle=999)

    assert res.status == IdentityStatus.NOT_IN_JOB
    assert res.is_safe_to_target is False


def test_identity_process_not_found() -> None:
    win32 = MagicMock()
    validator = IdentityValidator(win32=win32)
    validator._open_process_query = MagicMock(return_value=None)  # type: ignore[method-assign]

    res = validator.verify(_sample_identity(pid=999999, create_time=10.0))

    assert res.status == IdentityStatus.PROCESS_NOT_FOUND
    assert res.is_safe_to_target is False


def test_attempt_context_validation_rejects_missing_or_negative_fencing() -> None:
    bad_ctx = AttemptLaunchContext(
        execution_id="",
        attempt_id="att-001",
        job_id="job-001",
        tenant_id="tenant-01",
        channel_id="chan-01",
        worker_id="win-worker-01",
        fencing_token=1,
        browser_user_data_dir="C:/tmp/p",
    )
    with pytest.raises(ProcessLauncherError, match="execution_id"):
        bad_ctx.validate()

    neg_ctx = AttemptLaunchContext(
        execution_id="exec-001",
        attempt_id="att-001",
        job_id="job-001",
        tenant_id="tenant-01",
        channel_id="chan-01",
        worker_id="win-worker-01",
        fencing_token=-1,
        browser_user_data_dir="C:/tmp/p",
    )
    with pytest.raises(ProcessLauncherError, match="fencing_token"):
        neg_ctx.validate()


def test_build_upload_environment_sets_canonical_markers() -> None:
    ctx = _sample_ctx()
    env = build_upload_environment(ctx, base_env={"PATH": "C:\\Windows"})
    assert env["DCF_EXECUTION_ID"] == "exec-001"
    assert env["DCF_ATTEMPT_ID"] == "att-001"
    assert env["DCF_FENCING_TOKEN"] == "7"
    assert "DCF_JOB_OBJECT_NAME" not in env
    assert env["PATH"] == "C:\\Windows"


def test_pre_resume_hook_failure_aborts_suspended_process_before_resume() -> None:
    from desktop_worker.watchdog.job_object import JobObjectProcessManager

    ctx = _sample_ctx()
    win32 = MagicMock()
    win32.create_job_object.return_value = 100
    win32.set_information_job_object.return_value = True
    proc_info = MagicMock(hProcess=200, hThread=300, dwProcessId=4321)
    win32.create_process_suspended.return_value = proc_info
    win32.assign_process_to_job_object.return_value = True
    win32.is_process_in_job.return_value = (True, True)
    win32.get_process_creation_time.return_value = 133000000000000000
    win32.terminate_job_object.return_value = True
    win32.close_handle.return_value = True

    mgr = JobObjectProcessManager(api=win32)

    def failing_hook(_identity: CanonicalExecutionIdentity) -> None:
        raise RuntimeError("SQLite persistence failed")

    with pytest.raises(ProcessLauncherError, match="pre_resume_hook failed"):
        launch_isolated_upload(
            ["python", "-c", "pass"],
            ctx,
            pre_resume_hook=failing_hook,
            manager=mgr,
        )

    win32.resume_thread.assert_not_called()
    win32.terminate_process.assert_called_once_with(200, 1)


def test_launch_failure_in_job_manager_raises_process_launcher_error() -> None:
    ctx = _sample_ctx()
    mock_mgr = MagicMock()
    mock_launch = MagicMock()
    mock_launch.ok = False
    mock_launch.error.message = "AssignProcessToJobObject failed"
    mock_mgr.launch.return_value = mock_launch

    with pytest.raises(ProcessLauncherError, match="AssignProcessToJobObject failed"):
        launch_isolated_upload(["python", "-c", "pass"], ctx, manager=mock_mgr)


def test_resume_thread_called_only_after_pre_resume_hook_succeeds() -> None:
    from desktop_worker.watchdog.job_object import JobObjectProcessManager

    ctx = _sample_ctx()
    win32 = MagicMock()
    win32.create_job_object.return_value = 100
    win32.set_information_job_object.return_value = True
    proc_info = MagicMock(hProcess=200, hThread=300, dwProcessId=4321)
    win32.create_process_suspended.return_value = proc_info
    win32.assign_process_to_job_object.return_value = True
    win32.is_process_in_job.return_value = (True, True)
    win32.get_process_creation_time.return_value = 133000000000000000
    win32.resume_thread.return_value = 1

    mgr = JobObjectProcessManager(api=win32)
    events: list[str] = []

    def hook(ident: CanonicalExecutionIdentity) -> None:
        assert ident.pid == 4321
        assert ident.process_create_time == 133000000000000000
        win32.resume_thread.assert_not_called()
        events.append("hook_done")

    win32.resume_thread.side_effect = lambda _h: (events.append("resumed"), 1)[1]

    res = launch_isolated_upload(
        ["python", "-c", "pass"],
        ctx,
        pre_resume_hook=hook,
        manager=mgr,
    )

    assert events == ["hook_done", "resumed"]
    assert res.identity.pid == 4321


def test_unverifiable_when_job_association_cannot_be_verified() -> None:
    win32 = MagicMock()
    win32.get_process_creation_time.return_value = 133000000000000000
    validator = IdentityValidator(win32=win32)
    validator._open_process_query = MagicMock(return_value=1234)  # type: ignore[method-assign]
    validator._check_process_in_named_job = MagicMock(return_value=None)  # type: ignore[method-assign]

    res = validator.verify(_sample_identity(pid=100, create_time=133000000000000000))

    assert res.status == IdentityStatus.UNVERIFIABLE
    assert res.is_valid is False
    assert res.is_safe_to_target is False
    assert res.in_expected_job is None


def test_process_identity_and_to_process_identity_compatibility() -> None:
    win32 = MagicMock()
    win32.get_process_creation_time.return_value = 133000000000000000
    win32.is_process_in_job.return_value = (True, True)
    validator = IdentityValidator(win32=win32)
    validator._open_process_query = MagicMock(return_value=1234)  # type: ignore[method-assign]

    canonical = _sample_identity(pid=100, create_time=133000000000000000)
    proc_ident = canonical.to_process_identity(job_handle=999)

    res = validator.verify(proc_ident)
    assert res.status == IdentityStatus.VALID
    assert res.in_expected_job is True
    win32.is_process_in_job.assert_called_once_with(1234, 999)


def test_launch_isolated_upload_does_not_mutate_os_environ() -> None:
    import os
    from desktop_worker.watchdog.job_object import JobObjectProcessManager

    ctx = _sample_ctx()
    win32 = MagicMock()
    win32.create_job_object.return_value = 100
    win32.set_information_job_object.return_value = True
    proc_info = MagicMock(hProcess=200, hThread=300, dwProcessId=4321)
    win32.create_process_suspended.return_value = proc_info
    win32.assign_process_to_job_object.return_value = True
    win32.is_process_in_job.return_value = (True, True)
    win32.get_process_creation_time.return_value = 133000000000000000
    win32.resume_thread.return_value = 1

    mgr = JobObjectProcessManager(api=win32)
    os.environ.pop("DCF_EXECUTION_ID", None)

    launch_isolated_upload(["python", "-c", "pass"], ctx, manager=mgr)

    assert "DCF_EXECUTION_ID" not in os.environ
    call_kwargs = win32.create_process_suspended.call_args.kwargs
    assert call_kwargs["env"]["DCF_EXECUTION_ID"] == "exec-001"


def test_identity_with_real_job_handle_returns_valid() -> None:
    win32 = MagicMock()
    win32.get_process_creation_time.return_value = 133000000000000000
    win32.is_process_in_job.return_value = (True, True)

    validator = IdentityValidator(win32=win32)
    validator._open_process_query = MagicMock(return_value=1234)  # type: ignore[method-assign]

    ident = _sample_identity(pid=500, create_time=133000000000000000)
    res = validator.verify(ident, job_handle=777)

    assert res.status == IdentityStatus.VALID
    assert res.is_valid is True
    win32.is_process_in_job.assert_called_once_with(1234, 777)


def test_identity_without_job_handle_returns_unverifiable_even_with_synthetic_name() -> None:
    win32 = MagicMock()
    win32.get_process_creation_time.return_value = 133000000000000000
    # Even if an external named Job Object with the same synthetic name existed,
    # OpenJobObjectW must NOT be called to validate an anonymous Job Object.
    win32._kernel32 = MagicMock()
    win32._kernel32.OpenJobObjectW.return_value = 9999

    validator = IdentityValidator(win32=win32)
    validator._open_process_query = MagicMock(return_value=1234)  # type: ignore[method-assign]

    ident = _sample_identity(pid=500, create_time=133000000000000000)
    res = validator.verify(ident, job_handle=None)

    assert res.status == IdentityStatus.UNVERIFIABLE
    assert res.is_valid is False
    win32._kernel32.OpenJobObjectW.assert_not_called()


def test_identity_job_query_failure_returns_unverifiable() -> None:
    win32 = MagicMock()
    win32.get_process_creation_time.return_value = 133000000000000000
    win32.is_process_in_job.return_value = (False, False)

    validator = IdentityValidator(win32=win32)
    validator._open_process_query = MagicMock(return_value=1234)  # type: ignore[method-assign]

    ident = _sample_identity(pid=500, create_time=133000000000000000)
    res = validator.verify(ident, job_handle=777)

    assert res.status == IdentityStatus.UNVERIFIABLE
    assert res.is_valid is False
