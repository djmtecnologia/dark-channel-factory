"""Windows integration tests for Task 2: Process Launcher + IdentityValidator."""

from __future__ import annotations

import sys
import uuid

import pytest

from desktop_worker.watchdog.identity import (
    CanonicalExecutionIdentity,
    IdentityStatus,
    IdentityValidator,
)
from desktop_worker.watchdog.job_object import TerminationStatus
from desktop_worker.watchdog.process_launcher import (
    AttemptLaunchContext,
    launch_isolated_upload,
)


pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows-only Job Object integration test"
)


def test_wi01_launch_isolated_upload_and_validate_identity() -> None:
    """WI-01: Launch real Python subprocess in Job Object, verify identity, and terminate."""
    uid = uuid.uuid4().hex[:8]
    ctx = AttemptLaunchContext(
        execution_id=f"exec-{uid}",
        attempt_id=f"att-{uid}",
        job_id=f"job-{uid}",
        tenant_id="tenant-test",
        channel_id="chan-test",
        worker_id="win-test-worker",
        fencing_token=1,
        browser_user_data_dir=f"C:/tmp/profile_{uid}",
    )

    hook_called_with: list[CanonicalExecutionIdentity] = []

    res = launch_isolated_upload(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        ctx,
        pre_resume_hook=lambda ident: hook_called_with.append(ident),
    )

    try:
        assert len(hook_called_with) == 1
        assert res.identity.pid > 0
        assert res.identity.process_create_time > 0
        assert res.identity.job_object_name == ctx.resolved_job_object_name()

        validator = IdentityValidator()
        job_handle = res.launch_result.process.identity.job_handle
        assert job_handle is not None

        valid_check = validator.verify(res.identity, job_handle=job_handle)
        assert valid_check.status == IdentityStatus.VALID
        assert valid_check.is_safe_to_target is True
        assert valid_check.in_expected_job is True

        # Without job_handle on an unnamed Job Object, verification must be UNVERIFIABLE
        unverifiable_check = validator.verify(res.identity)
        assert unverifiable_check.status == IdentityStatus.UNVERIFIABLE
        assert unverifiable_check.is_safe_to_target is False
        assert unverifiable_check.in_expected_job is None

        # Simulate PID reuse by altering expected process_create_time
        tampered = CanonicalExecutionIdentity(
            **{**res.identity.__dict__, "process_create_time": res.identity.process_create_time + 100}
        )
        reused_check = validator.verify(tampered, job_handle=job_handle)
        assert reused_check.status == IdentityStatus.PID_REUSED
        assert reused_check.is_safe_to_target is False

        term = res.launch_result.process.terminate_job()
        assert term.status == TerminationStatus.TREE_TERMINATED
        assert term.active_processes == 0
    finally:
        if res.launch_result.process is not None:
            res.launch_result.process.close()


def test_wi02_isolated_environment_blocks_without_os_environ_mutation(tmp_path) -> None:
    """WI-02: Verify real subprocesses receive isolated lpEnvironment via CREATE_UNICODE_ENVIRONMENT."""
    import os

    out1 = tmp_path / "env1.txt"
    out2 = tmp_path / "env2.txt"

    def _ctx(tag: str) -> AttemptLaunchContext:
        return AttemptLaunchContext(
            execution_id=f"exec-{tag}",
            attempt_id=f"att-{tag}",
            job_id=f"job-{tag}",
            tenant_id="tenant-test",
            channel_id="chan-test",
            worker_id="win-test-worker",
            fencing_token=1,
            browser_user_data_dir=f"C:/tmp/profile_{tag}",
        )

    os.environ.pop("DCF_EXECUTION_ID", None)
    r1 = launch_isolated_upload(
        [sys.executable, "-c", f"import os, pathlib; pathlib.Path(r'{out1}').write_text(os.environ['DCF_EXECUTION_ID'])"],
        _ctx("alpha"),
    )
    r2 = launch_isolated_upload(
        [sys.executable, "-c", f"import os, pathlib; pathlib.Path(r'{out2}').write_text(os.environ['DCF_EXECUTION_ID'])"],
        _ctx("beta"),
    )
    try:
        assert r1.launch_result.process._api.wait_for_single_object(r1.launch_result.process.identity.process_handle, 5000) == 0
        assert r2.launch_result.process._api.wait_for_single_object(r2.launch_result.process.identity.process_handle, 5000) == 0
        assert "DCF_EXECUTION_ID" not in os.environ
        assert out1.read_text() == "exec-alpha"
        assert out2.read_text() == "exec-beta"
    finally:
        r1.launch_result.process.terminate_job()
        r1.launch_result.process.close()
        r2.launch_result.process.terminate_job()
        r2.launch_result.process.close()
