"""Isolated upload process launcher using Windows Job Objects (Task 2)."""

from __future__ import annotations

import os
from dataclasses import dataclass
import subprocess
from typing import Callable, Dict, Mapping, Optional, Sequence, Union

from desktop_worker.watchdog.identity import CanonicalExecutionIdentity
from desktop_worker.watchdog.job_object import (
    CREATE_NO_WINDOW,
    JobObjectProcessManager,
    LaunchResult,
    LauncherState,
)


class ProcessLauncherError(RuntimeError):
    """Raised when launching an isolated upload process fails."""


@dataclass(frozen=True)
class AttemptLaunchContext:
    """Context required to launch an isolated upload attempt."""

    execution_id: str
    attempt_id: str
    job_id: str
    tenant_id: str
    channel_id: str
    worker_id: str
    fencing_token: int
    browser_user_data_dir: str
    job_object_name: Optional[str] = None

    def resolved_job_object_name(self) -> str:
        """Return explicit job_object_name or logical identifier Local\\DCF_UPLOAD_<attempt>_<exec>.

        Task 1 creates anonymous Win32 Job Objects (``CreateJobObjectW(None)``),
        so this value is a logical identifier for correlation/observability only,
        not a real Win32 kernel object name.
        """
        if self.job_object_name and self.job_object_name.strip():
            return self.job_object_name.strip()
        return f"Local\\DCF_UPLOAD_{self.attempt_id}_{self.execution_id}"

    def validate(self) -> None:
        """Validate all required fields before process creation."""
        required = {
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "job_id": self.job_id,
            "tenant_id": self.tenant_id,
            "channel_id": self.channel_id,
            "worker_id": self.worker_id,
            "browser_user_data_dir": self.browser_user_data_dir,
        }
        missing = [k for k, v in required.items() if not v or not str(v).strip()]
        if missing:
            raise ProcessLauncherError(
                f"Missing required AttemptLaunchContext fields: {', '.join(missing)}"
            )
        if self.fencing_token < 0:
            raise ProcessLauncherError("fencing_token must be non-negative")


@dataclass
class IsolatedUploadLaunchResult:
    """Result of launching an isolated upload subprocess."""

    identity: CanonicalExecutionIdentity
    launch_result: LaunchResult
    job_manager: JobObjectProcessManager


def build_upload_environment(
    attempt_ctx: AttemptLaunchContext,
    base_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Build isolated environment variables with canonical attempt markers."""
    env = dict(os.environ if base_env is None else base_env)
    env.update(
        {
            "DCF_EXECUTION_ID": attempt_ctx.execution_id,
            "DCF_ATTEMPT_ID": attempt_ctx.attempt_id,
            "DCF_JOB_ID": attempt_ctx.job_id,
            "DCF_TENANT_ID": attempt_ctx.tenant_id,
            "DCF_CHANNEL_ID": attempt_ctx.channel_id,
            "DCF_WORKER_ID": attempt_ctx.worker_id,
            "DCF_FENCING_TOKEN": str(attempt_ctx.fencing_token),
            "DCF_BROWSER_USER_DATA_DIR": attempt_ctx.browser_user_data_dir,
        }
    )
    return env


def launch_isolated_upload(
    cmd: Union[str, Sequence[str]],
    attempt_ctx: AttemptLaunchContext,
    *,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[str] = None,
    pre_resume_hook: Optional[Callable[[CanonicalExecutionIdentity], None]] = None,
    manager: Optional[JobObjectProcessManager] = None,
) -> IsolatedUploadLaunchResult:
    """Launch an upload subprocess inside an exclusive Windows Job Object.

    Flow:
    1. Validate attempt_ctx and build isolated environment.
    2. Create Job Object (KILL_ON_JOB_CLOSE, no breakaway).
    3. Create process with CREATE_SUSPENDED.
    4. AssignProcessToJobObject + IsProcessInJob validation.
    5. Run optional pre_resume_hook (e.g. durable persistence) before ResumeThread.
    6. ResumeThread only after all validations succeed.
    """
    attempt_ctx.validate()
    job_name = attempt_ctx.resolved_job_object_name()
    merged_env = build_upload_environment(attempt_ctx, base_env=env)
    command_line = cmd if isinstance(cmd, str) else subprocess.list2cmdline(list(cmd))

    job_mgr = manager or JobObjectProcessManager()

    def _build_canonical_identity(proc_ident) -> CanonicalExecutionIdentity:
        return CanonicalExecutionIdentity(
            execution_id=attempt_ctx.execution_id,
            attempt_id=attempt_ctx.attempt_id,
            job_id=attempt_ctx.job_id,
            tenant_id=attempt_ctx.tenant_id,
            channel_id=attempt_ctx.channel_id,
            worker_id=attempt_ctx.worker_id,
            fencing_token=attempt_ctx.fencing_token,
            pid=proc_ident.pid,
            process_create_time=int(proc_ident.process_creation_time),
            job_object_name=getattr(proc_ident, "job_object_name", None) or job_name,
            browser_user_data_dir=attempt_ctx.browser_user_data_dir,
        )

    def _hook_adapter(proc_ident) -> None:
        if pre_resume_hook is not None:
            pre_resume_hook(_build_canonical_identity(proc_ident))

    launch_res = job_mgr.launch(
        command_line=command_line,
        working_dir=cwd,
        creation_flags=CREATE_NO_WINDOW,
        env=merged_env,
        pre_resume_hook=_hook_adapter if pre_resume_hook is not None else None,
    )

    if not launch_res.ok or launch_res.process is None:
        err_msg = launch_res.error.message if launch_res.error else "Unknown launch failure"
        raise ProcessLauncherError(f"Failed to launch isolated upload in Job: {err_msg}")

    identity = _build_canonical_identity(launch_res.process.identity)

    return IsolatedUploadLaunchResult(
        identity=identity,
        launch_result=launch_res,
        job_manager=job_mgr,
    )
