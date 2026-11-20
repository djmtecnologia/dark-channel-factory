"""Safe Process Termination Engine for Local Watchdog (Sprint 2.7 - Task 6).

Provides controlled, identity-verified termination of orphaned upload process
trees via Windows Job Objects, independent post-termination verification,
durable evidence persistence, and Outbox event recording.

Security invariants:
- Never terminates a process without full IdentityValidator == VALID proof.
- Never uses taskkill /IM, process name matching, os.kill(), or unvalidated TerminateProcess.
- Never treats job_object_name as a Win32 Job Object handle.
- Never treats TerminateJobObject returning True, timeouts, or interruption as
  proof of tree termination.
- Never implements or authorizes retries.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, List, Optional

from desktop_worker.watchdog.identity import (
    CanonicalExecutionIdentity,
    IdentityStatus,
    IdentityValidator,
    IdentityVerificationResult,
)
from desktop_worker.watchdog.job_handle_transfer import (
    AuthorizedJobHandle,
    Win32HandleTransferApi,
)
from desktop_worker.watchdog.job_object import (
    ERROR_MORE_DATA,
    STILL_ACTIVE,
    WAIT_FAILED,
    WAIT_OBJECT_0,
    BasicAccountingInfo,
    ManagedJobProcess,
    ProcessIdListResult,
    Win32Api,
)
from desktop_worker.watchdog.outbox import OutboxEventRecord, OutboxStore
from desktop_worker.watchdog.state_store import (
    AttemptRecord,
    CheckpointRecord,
    ExecutionRecord,
    ExecutionStatus,
    ProcessIdentityRecord,
    StateStore,
    StateStoreCorruptionError,
    StateStoreError,
    TerminationEvidenceRecord,
    _attempt_checksum_payload,
    _execution_checksum_payload,
    _process_identity_checksum_payload,
    compute_canonical_checksum,
)
from desktop_worker.watchdog.termination_evidence import (
    EvidencePersistenceResult,
    TerminationEvidencePayload,
    TerminationEvidenceRecorder,
    TerminationOutboxEventType,
    TerminationVerificationResult,
    utc_now_iso,
)
from desktop_worker.watchdog.watchdog_daemon import (
    WATCHDOG_VERSION,
    parse_utc_iso,
)

logger = logging.getLogger(__name__)

# Explicitly allowed execution statuses for automatic safe termination.
# CLOSED, INCONCLUSIVE, or any unknown/inconsistent status are strictly blocked.
ALLOWED_TERMINATION_STATUSES = frozenset(
    {
        ExecutionStatus.RUNNING,
        ExecutionStatus.TERMINATING,
    }
)


class OrphanClassification(str, Enum):
    """Explicit classification of an execution's orphan / termination eligibility."""

    NOT_ORPHANED = "NOT_ORPHANED"
    ORPHAN_CANDIDATE = "ORPHAN_CANDIDATE"
    SAFE_TO_TERMINATE = "SAFE_TO_TERMINATE"
    ALREADY_TERMINATED = "ALREADY_TERMINATED"
    INCONCLUSIVE = "INCONCLUSIVE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


@dataclass(frozen=True)
class OrphanDetectionResult:
    """Outcome of evaluating whether an execution is orphaned and safe to terminate."""

    classification: OrphanClassification
    execution_id: str
    attempt_id: str
    fencing_token: int
    identity_result: Optional[IdentityVerificationResult] = None
    reason: str = ""
    heartbeat_expired: bool = False
    different_boot: bool = False


@dataclass(frozen=True)
class TerminationContext:
    """Validated context required to request safe termination of an execution."""

    identity: CanonicalExecutionIdentity
    job_handle: Optional[int]
    process_handle: Optional[int] = None
    termination_reason: str = "ORPHAN_EXECUTION"
    evidence_source: str = "WATCHDOG_TERMINATION_ENGINE"
    event_id: Optional[str] = None
    expected_fencing_token: Optional[int] = None
    expected_job_handle: Optional[int] = None


@dataclass(frozen=True)
class SafeTerminationOutcome:
    """Structured result of a safe termination attempt."""

    verification_result: TerminationVerificationResult
    classification: OrphanClassification
    identity_status: IdentityStatus
    evidence_persisted: bool
    outbox_enqueued: bool
    evidence_record: Optional[TerminationEvidenceRecord] = None
    outbox_event: Optional[OutboxEventRecord] = None
    active_processes_before: Optional[int] = None
    active_processes_after: Optional[int] = None
    pids_before: Optional[List[int]] = None
    pids_after: Optional[List[int]] = None
    pids_truncated: bool = False
    wait_result: Optional[int] = None
    exit_code: Optional[int] = None
    reason: str = ""
    error_code: Optional[str] = None
    fully_completed: bool = False


class SafeTerminationEngine:
    """Controlled, identity-verified process tree termination engine."""

    def __init__(
        self,
        state_store: StateStore,
        *,
        outbox_store: Optional[OutboxStore] = None,
        win32: Optional[Win32Api] = None,
        identity_validator: Optional[IdentityValidator] = None,
        watchdog_version: str = WATCHDOG_VERSION,
        default_timeout_ms: int = 5000,
        poll_interval_ms: int = 50,
        stop_event: Optional[threading.Event] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if default_timeout_ms <= 0:
            raise ValueError("default_timeout_ms must be > 0")
        if poll_interval_ms <= 0:
            raise ValueError("poll_interval_ms must be > 0")

        self._state_store = state_store
        self._outbox_store = outbox_store
        self._win32 = win32 or Win32Api()
        self._identity_validator = identity_validator or IdentityValidator(win32=self._win32)
        self._recorder = TerminationEvidenceRecorder(
            state_store=state_store,
            outbox_store=outbox_store,
        )
        self._watchdog_version = watchdog_version
        self._default_timeout_ms = int(default_timeout_ms)
        self._poll_interval_ms = int(poll_interval_ms)
        self._stop_event = stop_event
        self._clock = clock or (lambda: datetime.now(timezone.utc))

        self._lock = threading.Lock()
        self._active_executions: set[str] = set()
        self._authorized_job_handles: Dict[str, AuthorizedJobHandle] = {}
        self._deferred_close_handles: Dict[str, List[int]] = {}
        self._handle_transfer_api: Optional[Win32HandleTransferApi] = (
            Win32HandleTransferApi()
            if (sys.platform == "win32" and isinstance(self._win32, Win32Api))
            else None
        )

    def _close_win32_handle(self, handle: int) -> bool:
        """Close a Win32 handle safely without raising."""
        if not handle or int(handle) <= 0:
            return False
        try:
            if self._handle_transfer_api is not None:
                return self._handle_transfer_api.close_handle(int(handle))
            if hasattr(self._win32, "close_handle"):
                return bool(self._win32.close_handle(int(handle)))
        except Exception as exc:
            logger.warning("Failed to close Job Object handle %s: %s", handle, exc)
        return False

    def register_job_handle(
        self,
        execution_id: str,
        job_handle: int,
        *,
        owned: bool = False,
        attempt_id: Optional[str] = None,
        fencing_token: Optional[int] = None,
        source_pid: Optional[int] = None,
        source_process_create_time: Optional[int] = None,
        watchdog_pid: Optional[int] = None,
        watchdog_process_create_time: Optional[int] = None,
        daemon_instance_id: Optional[str] = None,
    ) -> None:
        """Bind the exact authorized Win32 Job Object handle to an execution_id.

        Mandatory fail-closed authorization: no execution can be terminated
        unless its Job Object handle is explicitly registered here.
        If ``owned=True``, this engine takes ownership of the duplicated handle
        and will close it on ``unregister_job_handle`` or ``close()``.
        """
        if not execution_id or not execution_id.strip():
            raise ValueError("execution_id must be a non-empty string")
        if isinstance(job_handle, bool) or not isinstance(job_handle, int) or job_handle <= 0:
            raise ValueError("job_handle must be a positive integer handle")

        previous_owned: Optional[int] = None
        entry = AuthorizedJobHandle(
            execution_id=execution_id,
            job_handle=int(job_handle),
            owned_by_registry=bool(owned),
            attempt_id=attempt_id,
            fencing_token=fencing_token,
            source_pid=source_pid,
            source_process_create_time=source_process_create_time,
            watchdog_pid=watchdog_pid,
            watchdog_process_create_time=watchdog_process_create_time,
            daemon_instance_id=daemon_instance_id,
            registered_at_utc=utc_now_iso(),
        )
        with self._lock:
            prev = self._authorized_job_handles.get(execution_id)
            if (
                prev is not None
                and prev.owned_by_registry
                and prev.job_handle != int(job_handle)
            ):
                if execution_id in self._active_executions:
                    self._deferred_close_handles.setdefault(execution_id, []).append(
                        prev.job_handle
                    )
                else:
                    previous_owned = prev.job_handle
            self._authorized_job_handles[execution_id] = entry

        if previous_owned is not None:
            self._close_win32_handle(previous_owned)

    def accept_transferred_job_handle(
        self,
        execution_id: str,
        job_handle: int,
        *,
        attempt_id: Optional[str] = None,
        fencing_token: Optional[int] = None,
        source_pid: Optional[int] = None,
        source_process_create_time: Optional[int] = None,
        watchdog_pid: Optional[int] = None,
        watchdog_process_create_time: Optional[int] = None,
        daemon_instance_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """Validate and register a handle duplicated from an external Launcher process.

        Takes ownership of ``job_handle`` (``owned=True``). Requires full
        attempt binding (``execution_id``, ``attempt_id``, ``fencing_token``,
        ``source_pid``). If validation fails, closes the handle (when live)
        to prevent handle leaks.
        """
        if not execution_id or not execution_id.strip():
            if isinstance(job_handle, int) and not isinstance(job_handle, bool) and job_handle > 0:
                self._close_win32_handle(job_handle)
            return False, "execution_id must be non-empty"
        if isinstance(job_handle, bool) or not isinstance(job_handle, int) or job_handle <= 0:
            return False, "job_handle must be a positive integer"

        if not self._is_handle_valid(job_handle):
            return False, f"Transferred handle {job_handle} is invalid or closed"

        if not isinstance(attempt_id, str) or not attempt_id.strip():
            self._close_win32_handle(job_handle)
            return False, "attempt_id is required for transferred handle authorization"
        if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 0:
            self._close_win32_handle(job_handle)
            return False, "fencing_token is required for transferred handle authorization"
        if isinstance(source_pid, bool) or not isinstance(source_pid, int) or source_pid <= 0:
            self._close_win32_handle(job_handle)
            return False, "verified source_pid is required for transferred handle authorization"

        # Confirm the handle actually refers to a kernel Job Object
        acct = self._query_accounting(job_handle)
        if acct is None:
            self._close_win32_handle(job_handle)
            return False, f"Transferred handle {job_handle} is not a valid queryable Job Object"

        self.register_job_handle(
            execution_id,
            job_handle,
            owned=True,
            attempt_id=attempt_id.strip(),
            fencing_token=int(fencing_token),
            source_pid=int(source_pid),
            source_process_create_time=source_process_create_time,
            watchdog_pid=watchdog_pid,
            watchdog_process_create_time=watchdog_process_create_time,
            daemon_instance_id=daemon_instance_id,
        )
        return True, "Authorized Job Object handle registered"

    def unregister_job_handle(
        self,
        execution_id: str,
        *,
        close_if_owned: bool = True,
    ) -> bool:
        """Remove a previously bound authorized Job Object handle.

        Closes the handle if it was owned by this engine (e.g. duplicated from
        the Launcher process). If a termination for ``execution_id`` is currently
        in-flight, closing the handle is deferred until that call exits so the
        handle is never closed/recycled while in use, while the immediate removal
        from ``_authorized_job_handles`` causes the anti-TOCTOU check to block
        ``TerminateJobObject``.
        """
        handle_to_close: Optional[int] = None
        with self._lock:
            entry = self._authorized_job_handles.pop(execution_id, None)
            if entry is not None and entry.owned_by_registry and close_if_owned:
                if execution_id in self._active_executions:
                    self._deferred_close_handles.setdefault(execution_id, []).append(
                        entry.job_handle
                    )
                else:
                    handle_to_close = entry.job_handle
        if entry is None:
            return False
        if handle_to_close is not None:
            self._close_win32_handle(handle_to_close)
        return True

    def has_authorized_job_handle(self, execution_id: str) -> bool:
        """Return True if an authorized handle is currently registered."""
        with self._lock:
            return execution_id in self._authorized_job_handles

    def get_authorized_job_handle(self, execution_id: str) -> Optional[int]:
        """Return the currently registered handle value (in-process only), or None."""
        with self._lock:
            entry = self._authorized_job_handles.get(execution_id)
            return entry.job_handle if entry is not None else None

    def get_authorized_job_entry(self, execution_id: str) -> Optional[AuthorizedJobHandle]:
        """Return the full in-memory AuthorizedJobHandle binding, or None."""
        with self._lock:
            return self._authorized_job_handles.get(execution_id)

    def close(self) -> None:
        """Release and close all owned duplicated Job Object handles."""
        handles_to_close: List[int] = []
        with self._lock:
            entries = list(self._authorized_job_handles.values())
            self._authorized_job_handles.clear()
            for entry in entries:
                if entry.owned_by_registry:
                    if entry.execution_id in self._active_executions:
                        self._deferred_close_handles.setdefault(
                            entry.execution_id, []
                        ).append(entry.job_handle)
                    else:
                        handles_to_close.append(entry.job_handle)
        for handle in handles_to_close:
            self._close_win32_handle(handle)

    def _is_handle_valid(self, handle: int) -> bool:
        """Check whether ``handle`` is a live, open Win32 handle."""
        if isinstance(handle, bool) or not isinstance(handle, int) or handle <= 0:
            return False
        if hasattr(self._win32, "is_handle_valid"):
            try:
                return bool(self._win32.is_handle_valid(int(handle)))
            except Exception:
                return False
        if self._handle_transfer_api is not None:
            return self._handle_transfer_api.is_handle_valid(int(handle))
        return True

    def _handles_refer_to_same_job_object(self, handle_a: int, handle_b: int) -> bool:
        """Verify whether two live handles refer to the exact same kernel Job Object."""
        if (
            isinstance(handle_a, bool)
            or isinstance(handle_b, bool)
            or not isinstance(handle_a, int)
            or not isinstance(handle_b, int)
            or handle_a <= 0
            or handle_b <= 0
        ):
            return False
        if hasattr(self._win32, "compare_object_handles"):
            try:
                return bool(self._win32.compare_object_handles(int(handle_a), int(handle_b)))
            except Exception:
                return False
        if self._handle_transfer_api is not None:
            return self._handle_transfer_api.compare_object_handles(int(handle_a), int(handle_b))
        return int(handle_a) == int(handle_b)

    def _validate_authorized_job_handle(
        self,
        *,
        execution_id: str,
        attempt_id: str,
        fencing_token: int,
        job_handle: Optional[int],
        expected_job_handle: Optional[int],
        persisted_job_object_name: str,
    ) -> Optional[tuple[OrphanClassification, str, str]]:
        """Validate mandatory fail-closed Job Object handle authorization.

        Verifies binding to ``execution_id``, ``attempt_id``, ``fencing_token``,
        Launcher process identity (for IPC-transferred handles), and kernel
        Job Object identity.

        Returns ``None`` if authorized and valid, or
        ``(classification, reason, error_code)`` if blocked.
        """
        if job_handle is None or isinstance(job_handle, bool) or int(job_handle) <= 0:
            return (
                OrphanClassification.INCONCLUSIVE,
                "Cannot terminate without a real Win32 Job Object handle",
                "MISSING_JOB_HANDLE",
            )

        with self._lock:
            registered_entry = self._authorized_job_handles.get(execution_id)

        if registered_entry is None:
            return (
                OrphanClassification.INCONCLUSIVE,
                (
                    f"Blocked termination: no authorized Job Object handle registered for "
                    f"execution '{execution_id}' (fail-closed; expected_job_handle and "
                    "job_object_name cannot substitute for explicit authorization)"
                ),
                "JOB_HANDLE_NOT_AUTHORIZED",
            )

        if registered_entry.execution_id != execution_id:
            return (
                OrphanClassification.IDENTITY_MISMATCH,
                (
                    f"Blocked termination: registered authorization execution_id "
                    f"({registered_entry.execution_id!r}) != current execution_id ({execution_id!r})"
                ),
                "AUTHORIZED_EXECUTION_MISMATCH",
            )

        if registered_entry.owned_by_registry:
            if (
                not registered_entry.attempt_id
                or registered_entry.fencing_token is None
                or registered_entry.source_pid is None
                or registered_entry.source_pid <= 0
            ):
                return (
                    OrphanClassification.INCONCLUSIVE,
                    (
                        f"Blocked termination: IPC-transferred authorization for execution "
                        f"'{execution_id}' is missing required attempt/fencing/launcher binding"
                    ),
                    "INCOMPLETE_AUTHORIZATION_BINDING",
                )

        if (
            registered_entry.attempt_id is not None
            and registered_entry.attempt_id != attempt_id
        ):
            return (
                OrphanClassification.IDENTITY_MISMATCH,
                (
                    f"Blocked termination: registered authorization attempt_id "
                    f"({registered_entry.attempt_id!r}) != current attempt_id ({attempt_id!r}); "
                    "handle cannot be reused across attempts"
                ),
                "AUTHORIZED_ATTEMPT_MISMATCH",
            )

        if (
            registered_entry.fencing_token is not None
            and registered_entry.fencing_token != fencing_token
        ):
            return (
                OrphanClassification.IDENTITY_MISMATCH,
                (
                    f"Blocked termination: registered authorization fencing_token "
                    f"({registered_entry.fencing_token}) != current fencing_token ({fencing_token})"
                ),
                "AUTHORIZED_FENCING_TOKEN_MISMATCH",
            )

        if (
            registered_entry.watchdog_pid is not None
            and registered_entry.watchdog_pid != os.getpid()
        ):
            return (
                OrphanClassification.IDENTITY_MISMATCH,
                (
                    f"Blocked termination: registered authorization watchdog_pid "
                    f"({registered_entry.watchdog_pid}) != current watchdog pid ({os.getpid()})"
                ),
                "AUTHORIZED_WATCHDOG_INSTANCE_MISMATCH",
            )

        registered_handle = registered_entry.job_handle

        if not self._is_handle_valid(registered_handle):
            return (
                OrphanClassification.INCONCLUSIVE,
                (
                    f"Blocked termination: registered authorized Job Object handle "
                    f"({registered_handle}) for execution '{execution_id}' is closed or invalid"
                ),
                "INVALID_OR_CLOSED_JOB_HANDLE",
            )

        if not self._is_handle_valid(int(job_handle)):
            return (
                OrphanClassification.INCONCLUSIVE,
                (
                    f"Blocked termination: provided job_handle ({job_handle}) for "
                    f"execution '{execution_id}' is closed or invalid"
                ),
                "INVALID_OR_CLOSED_JOB_HANDLE",
            )

        if not self._handles_refer_to_same_job_object(int(job_handle), registered_handle):
            return (
                OrphanClassification.IDENTITY_MISMATCH,
                (
                    f"Blocked termination: job_handle ({job_handle}) does not refer to the "
                    f"same kernel Job Object as registered authorized handle ({registered_handle}) "
                    f"for execution '{execution_id}'"
                ),
                "JOB_HANDLE_MISMATCH",
            )

        if expected_job_handle is not None and not self._handles_refer_to_same_job_object(
            int(job_handle), int(expected_job_handle)
        ):
            return (
                OrphanClassification.IDENTITY_MISMATCH,
                (
                    f"Blocked termination: job_handle ({job_handle}) does not match "
                    f"expected authorized Job Object handle ({expected_job_handle}) "
                    f"for execution '{execution_id}'"
                ),
                "JOB_HANDLE_MISMATCH",
            )

        kernel_job_name = self._query_job_object_kernel_name(int(job_handle))
        if kernel_job_name:
            kernel_basename = kernel_job_name.rsplit("\\", 1)[-1]
            expected_basename = persisted_job_object_name.rsplit("\\", 1)[-1]
            if (
                kernel_job_name != persisted_job_object_name
                and kernel_basename != expected_basename
            ):
                return (
                    OrphanClassification.IDENTITY_MISMATCH,
                    (
                        f"Blocked termination: Job Object kernel name ({kernel_job_name!r}) "
                        f"does not match persisted job_object_name "
                        f"({persisted_job_object_name!r})"
                    ),
                    "JOB_HANDLE_NAME_MISMATCH",
                )

        return None

    @staticmethod
    def _query_job_object_kernel_name(job_handle: int) -> Optional[str]:
        """Query the Win32 kernel object name of a Job Object handle if named.

        Returns None for unnamed Job Objects or non-Win32/fake handles.
        """
        if sys.platform != "win32" or job_handle <= 0:
            return None
        try:
            from ctypes import wintypes

            class _UNICODE_STRING(ctypes.Structure):
                _fields_ = [
                    ("Length", wintypes.USHORT),
                    ("MaximumLength", wintypes.USHORT),
                    ("Buffer", wintypes.LPWSTR),
                ]

            ntdll = ctypes.WinDLL("ntdll")
            nt_query = ntdll.NtQueryObject
            nt_query.argtypes = [
                wintypes.HANDLE,
                wintypes.ULONG,
                wintypes.LPVOID,
                wintypes.ULONG,
                ctypes.POINTER(wintypes.ULONG),
            ]
            nt_query.restype = ctypes.c_long

            buf_size = 1024
            buf = ctypes.create_string_buffer(buf_size)
            ret_len = wintypes.ULONG(0)
            # ObjectNameInformation = 1
            status = nt_query(
                wintypes.HANDLE(int(job_handle)),
                1,
                buf,
                buf_size,
                ctypes.byref(ret_len),
            )
            if status != 0:
                return None
            ustr = _UNICODE_STRING.from_buffer(buf)
            if ustr.Length == 0 or not ustr.Buffer:
                return None
            return str(ustr.Buffer[: ustr.Length // 2])
        except Exception:
            return None

    # -----------------------------------------------------------------------
    # Orphan Detection (Section 3.1)
    # -----------------------------------------------------------------------

    def classify_orphan(
        self,
        execution_id: str,
        *,
        heartbeat_timeout_sec: float,
        job_handle: Optional[int] = None,
        current_boot_time_utc: Optional[str] = None,
        expected_fencing_token: Optional[int] = None,
    ) -> OrphanDetectionResult:
        """Classify an execution using persisted State Store records and live identity."""
        try:
            exec_rec: Optional[ExecutionRecord] = self._state_store.get_execution(execution_id)
        except StateStoreCorruptionError as exc:
            return OrphanDetectionResult(
                classification=OrphanClassification.INCONCLUSIVE,
                execution_id=execution_id,
                attempt_id="",
                fencing_token=-1,
                reason=f"Corrupted execution record (invalid checksum): {exc}",
            )
        except StateStoreError as exc:
            return OrphanDetectionResult(
                classification=OrphanClassification.INCONCLUSIVE,
                execution_id=execution_id,
                attempt_id="",
                fencing_token=-1,
                reason=f"Failed to read execution record: {exc}",
            )

        if exec_rec is None:
            return OrphanDetectionResult(
                classification=OrphanClassification.INCONCLUSIVE,
                execution_id=execution_id,
                attempt_id="",
                fencing_token=-1,
                reason=f"Execution '{execution_id}' not found in State Store",
            )

        if exec_rec.status == ExecutionStatus.INCONCLUSIVE:
            return OrphanDetectionResult(
                classification=OrphanClassification.INCONCLUSIVE,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                reason="Execution is already classified as INCONCLUSIVE",
            )

        if exec_rec.status == ExecutionStatus.CLOSED:
            return OrphanDetectionResult(
                classification=OrphanClassification.NOT_ORPHANED,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                reason="Execution is already CLOSED",
            )

        if (
            expected_fencing_token is not None
            and expected_fencing_token != exec_rec.fencing_token
        ):
            return OrphanDetectionResult(
                classification=OrphanClassification.IDENTITY_MISMATCH,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                reason=(
                    f"Fencing token mismatch: expected {expected_fencing_token}, "
                    f"persisted {exec_rec.fencing_token}"
                ),
            )

        try:
            identities: List[ProcessIdentityRecord] = self._state_store.get_process_identities(
                exec_rec.execution_id
            )
            attempt: Optional[AttemptRecord] = self._state_store.get_attempt(exec_rec.attempt_id)
            checkpoint: Optional[CheckpointRecord] = self._state_store.get_checkpoint(
                exec_rec.worker_id
            )
        except StateStoreError as exc:
            return OrphanDetectionResult(
                classification=OrphanClassification.INCONCLUSIVE,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                reason=f"State Store error loading attempt/identity/checkpoint: {exc}",
            )

        if not identities:
            return OrphanDetectionResult(
                classification=OrphanClassification.INCONCLUSIVE,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                reason="Incomplete identity: no persisted ProcessIdentityRecord",
            )

        primary_identity = identities[0]
        if (
            primary_identity.pid <= 0
            or primary_identity.process_create_time <= 0
            or not primary_identity.job_object_name.strip()
        ):
            return OrphanDetectionResult(
                classification=OrphanClassification.INCONCLUSIVE,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                reason="Incomplete ProcessIdentityRecord fields",
            )

        if attempt is None or not attempt.last_heartbeat_at_utc:
            return OrphanDetectionResult(
                classification=OrphanClassification.INCONCLUSIVE,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                reason="Missing AttemptRecord or last_heartbeat_at_utc",
            )

        if attempt.fencing_token != exec_rec.fencing_token:
            return OrphanDetectionResult(
                classification=OrphanClassification.IDENTITY_MISMATCH,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                reason=(
                    f"Attempt fencing_token ({attempt.fencing_token}) != "
                    f"execution fencing_token ({exec_rec.fencing_token})"
                ),
            )

        # Check boot time caution
        effective_boot = current_boot_time_utc or (
            checkpoint.last_boot_time_utc if checkpoint is not None else None
        )
        different_boot = bool(
            effective_boot and exec_rec.boot_time_utc and exec_rec.boot_time_utc != effective_boot
        )

        try:
            hb_dt = parse_utc_iso(attempt.last_heartbeat_at_utc)
        except ValueError as exc:
            return OrphanDetectionResult(
                classification=OrphanClassification.INCONCLUSIVE,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                different_boot=different_boot,
                reason=f"Invalid heartbeat timestamp: {exc}",
            )

        age_sec = (self._clock() - hb_dt).total_seconds()
        heartbeat_expired = age_sec > heartbeat_timeout_sec

        if not heartbeat_expired and not different_boot:
            return OrphanDetectionResult(
                classification=OrphanClassification.NOT_ORPHANED,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                heartbeat_expired=False,
                different_boot=False,
                reason="Heartbeat is active within timeout and boot matches",
            )

        if different_boot:
            return OrphanDetectionResult(
                classification=OrphanClassification.INCONCLUSIVE,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                heartbeat_expired=heartbeat_expired,
                different_boot=True,
                reason=(
                    f"Execution boot_time_utc ({exec_rec.boot_time_utc}) differs from "
                    f"current boot ({effective_boot}); requires cautious verification"
                ),
            )

        # Heartbeat expired alone is only ORPHAN_CANDIDATE until full identity + job handle check
        if job_handle is None or job_handle <= 0:
            return OrphanDetectionResult(
                classification=OrphanClassification.ORPHAN_CANDIDATE,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                heartbeat_expired=True,
                different_boot=False,
                reason=(
                    "Heartbeat expired; classified as ORPHAN_CANDIDATE "
                    "(real job_handle required before authorizing termination)"
                ),
            )

        canonical = CanonicalExecutionIdentity(
            execution_id=exec_rec.execution_id,
            attempt_id=exec_rec.attempt_id,
            job_id=exec_rec.job_id,
            tenant_id=exec_rec.tenant_id,
            channel_id=exec_rec.channel_id,
            worker_id=exec_rec.worker_id,
            fencing_token=exec_rec.fencing_token,
            pid=primary_identity.pid,
            process_create_time=primary_identity.process_create_time,
            job_object_name=primary_identity.job_object_name,
            browser_user_data_dir=exec_rec.browser_user_data_dir,
        )
        id_res = self._identity_validator.verify(canonical, job_handle=job_handle)

        if id_res.status == IdentityStatus.VALID:
            return OrphanDetectionResult(
                classification=OrphanClassification.SAFE_TO_TERMINATE,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                identity_result=id_res,
                heartbeat_expired=True,
                different_boot=False,
                reason="Heartbeat expired and full process/Job Object identity verified",
            )

        if id_res.status == IdentityStatus.PROCESS_NOT_FOUND:
            return OrphanDetectionResult(
                classification=OrphanClassification.ALREADY_TERMINATED,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                identity_result=id_res,
                heartbeat_expired=True,
                different_boot=False,
                reason="Process not found during identity validation; verify Job Object tree",
            )

        if id_res.status in (IdentityStatus.PID_REUSED, IdentityStatus.NOT_IN_JOB):
            return OrphanDetectionResult(
                classification=OrphanClassification.IDENTITY_MISMATCH,
                execution_id=exec_rec.execution_id,
                attempt_id=exec_rec.attempt_id,
                fencing_token=exec_rec.fencing_token,
                identity_result=id_res,
                heartbeat_expired=True,
                different_boot=False,
                reason=id_res.reason,
            )

        return OrphanDetectionResult(
            classification=OrphanClassification.INCONCLUSIVE,
            execution_id=exec_rec.execution_id,
            attempt_id=exec_rec.attempt_id,
            fencing_token=exec_rec.fencing_token,
            identity_result=id_res,
            heartbeat_expired=True,
            different_boot=False,
            reason=id_res.reason or "Identity unverifiable",
        )

    # -----------------------------------------------------------------------
    # Job Object Query Helpers (reusing Task 1 ManagedJobProcess / Win32Api)
    # -----------------------------------------------------------------------

    def _make_job_helper(self, job_handle: int) -> ManagedJobProcess:
        dummy_identity = CanonicalExecutionIdentity(
            execution_id="",
            attempt_id="",
            job_id="",
            tenant_id="",
            channel_id="",
            worker_id="",
            fencing_token=0,
            pid=0,
            process_create_time=0,
            job_object_name="",
            browser_user_data_dir="",
        ).to_process_identity(job_handle=job_handle)
        return ManagedJobProcess(self._win32, dummy_identity)

    def _query_accounting(self, job_handle: int) -> Optional[BasicAccountingInfo]:
        try:
            acct, err = self._make_job_helper(job_handle).query_basic_accounting()
            if err is not None:
                return None
            return acct
        except Exception as exc:
            logger.warning("QueryInformationJobObject(Accounting) raised: %s", exc)
            return None

    def _query_pid_list(self, job_handle: int) -> ProcessIdListResult:
        """Query Job Object PID list with buffer growth and truncation detection."""
        return self._make_job_helper(job_handle).query_process_id_list()

    # -----------------------------------------------------------------------
    # Safe Termination Execution (Sections 3.2 - 3.6)
    # -----------------------------------------------------------------------

    def terminate_execution(
        self,
        context: TerminationContext,
        *,
        timeout_ms: Optional[int] = None,
        exit_code: int = 1,
    ) -> SafeTerminationOutcome:
        """Safely terminate an execution's Job Object tree and persist verified evidence."""
        exec_id = context.identity.execution_id
        effective_timeout_ms = (
            self._default_timeout_ms if timeout_ms is None else int(timeout_ms)
        )
        if effective_timeout_ms <= 0:
            raise ValueError("timeout_ms must be > 0")

        with self._lock:
            if exec_id in self._active_executions:
                return SafeTerminationOutcome(
                    verification_result=TerminationVerificationResult.INCONCLUSIVE,
                    classification=OrphanClassification.INCONCLUSIVE,
                    identity_status=IdentityStatus.UNVERIFIABLE,
                    evidence_persisted=False,
                    outbox_enqueued=False,
                    reason=f"Concurrent termination already in progress for execution '{exec_id}'",
                    error_code="CONCURRENT_TERMINATION_IN_PROGRESS",
                    fully_completed=False,
                )
            self._active_executions.add(exec_id)

        try:
            return self._execute_termination(
                context=context,
                timeout_ms=effective_timeout_ms,
                exit_code=exit_code,
            )
        finally:
            deferred: List[int] = []
            with self._lock:
                self._active_executions.discard(exec_id)
                deferred = self._deferred_close_handles.pop(exec_id, [])
            for handle in deferred:
                self._close_win32_handle(handle)

    def _persist_and_build_outcome(
        self,
        *,
        context: TerminationContext,
        classification: OrphanClassification,
        identity_result: IdentityVerificationResult,
        verification_result: TerminationVerificationResult,
        termination_request_ok: Optional[bool],
        wait_result: Optional[int],
        active_before: Optional[int],
        active_after: Optional[int],
        pids_before: Optional[List[int]],
        pids_after: Optional[List[int]],
        pids_truncated: bool,
        requested_at_utc: str,
        verified_at_utc: str,
        exit_code: Optional[int],
        reason: str,
        error_code: Optional[str] = None,
        outbox_event_type: Optional[TerminationOutboxEventType] = None,
        persist: bool = True,
    ) -> SafeTerminationOutcome:
        payload = TerminationEvidencePayload(
            execution_id=context.identity.execution_id,
            attempt_id=context.identity.attempt_id,
            job_id=context.identity.job_id,
            worker_id=context.identity.worker_id,
            fencing_token=context.identity.fencing_token,
            pid=context.identity.pid,
            process_create_time=context.identity.process_create_time,
            job_object_name=context.identity.job_object_name,
            identity_validation_status=identity_result.status.value,
            identity_validation_reason=identity_result.reason,
            termination_request_ok=termination_request_ok,
            wait_result=wait_result,
            active_processes_before=active_before,
            active_processes_after=active_after,
            pids_before=pids_before,
            pids_after=pids_after,
            pids_truncated=pids_truncated,
            termination_requested_at_utc=requested_at_utc,
            verified_at_utc=verified_at_utc,
            exit_code=exit_code,
            termination_reason=context.termination_reason,
            watchdog_version=self._watchdog_version,
            verification_result=verification_result,
            error_code=error_code,
            error_message=reason if error_code else None,
        )

        persistence_res = EvidencePersistenceResult(persisted=False)
        if persist:
            evidence_rec = self._recorder.build_evidence_record(
                payload,
                event_id=context.event_id,
                evidence_source=context.evidence_source,
            )
            persistence_res = self._recorder.persist(
                evidence_rec,
                outbox_event_type=outbox_event_type,
            )

        outbox_ok = persistence_res.outbox_event is not None
        final_reason = reason
        final_error_code = error_code
        if persist and not persistence_res.persisted:
            final_error_code = final_error_code or "EVIDENCE_PERSISTENCE_FAILED"
            final_reason = (
                f"{reason} (persistence failed: {persistence_res.error})"
                if reason
                else f"Evidence persistence failed: {persistence_res.error}"
            )

        fully_completed = bool(
            persistence_res.persisted
            and (self._outbox_store is None or outbox_ok)
            and verification_result == TerminationVerificationResult.TREE_TERMINATED
        )

        return SafeTerminationOutcome(
            verification_result=verification_result,
            classification=classification,
            identity_status=identity_result.status,
            evidence_persisted=persistence_res.persisted,
            outbox_enqueued=outbox_ok,
            evidence_record=persistence_res.evidence_record,
            outbox_event=persistence_res.outbox_event,
            active_processes_before=active_before,
            active_processes_after=active_after,
            pids_before=pids_before,
            pids_after=pids_after,
            pids_truncated=pids_truncated,
            wait_result=wait_result,
            exit_code=exit_code,
            reason=final_reason,
            error_code=final_error_code,
            fully_completed=fully_completed,
        )

    def _execute_termination(
        self,
        *,
        context: TerminationContext,
        timeout_ms: int,
        exit_code: int,
    ) -> SafeTerminationOutcome:
        requested_at_utc = utc_now_iso()
        unverifiable_id = IdentityVerificationResult(
            status=IdentityStatus.UNVERIFIABLE,
            pid=context.identity.pid,
            expected_create_time=context.identity.process_create_time,
            reason="Identity validation not executed",
        )

        def _reject_precheck(
            *,
            classification: OrphanClassification,
            reason: str,
            error_code: str,
            termination_request_ok: Optional[bool] = False,
            identity_reason: Optional[str] = None,
        ) -> SafeTerminationOutcome:
            logger.warning(
                "Blocked termination pre-check (%s): %s",
                error_code,
                reason,
                extra={
                    "execution_id": context.identity.execution_id,
                    "attempt_id": context.identity.attempt_id,
                    "error_code": error_code,
                },
            )
            return self._persist_and_build_outcome(
                context=context,
                classification=classification,
                identity_result=IdentityVerificationResult(
                    status=IdentityStatus.UNVERIFIABLE,
                    pid=context.identity.pid,
                    expected_create_time=context.identity.process_create_time,
                    reason=identity_reason or reason,
                ),
                verification_result=TerminationVerificationResult.INCONCLUSIVE,
                termination_request_ok=termination_request_ok,
                wait_result=None,
                active_before=None,
                active_after=None,
                pids_before=None,
                pids_after=None,
                pids_truncated=False,
                requested_at_utc=requested_at_utc,
                verified_at_utc=utc_now_iso(),
                exit_code=None,
                reason=reason,
                error_code=error_code,
                persist=False,
            )

        # ------------------------------------------------------------------
        # Step 1: Read ExecutionRecord
        # ------------------------------------------------------------------
        try:
            exec_rec = self._state_store.get_execution(context.identity.execution_id)
        except StateStoreCorruptionError as exc:
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=f"Corrupted ExecutionRecord (invalid checksum): {exc}",
                error_code="EXECUTION_RECORD_CORRUPTED",
                termination_request_ok=None,
            )
        except StateStoreError as exc:
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=f"State Store error loading execution: {exc}",
                error_code="STATE_STORE_ERROR",
                termination_request_ok=None,
            )

        if exec_rec is None:
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=f"Execution '{context.identity.execution_id}' not found in State Store",
                error_code="EXECUTION_NOT_FOUND",
                termination_request_ok=None,
            )

        # ------------------------------------------------------------------
        # Step 2: Validate ExecutionRecord integrity / checksum
        # ------------------------------------------------------------------
        expected_exec_checksum = compute_canonical_checksum(_execution_checksum_payload(exec_rec))
        if not exec_rec.checksum_sha256 or exec_rec.checksum_sha256 != expected_exec_checksum:
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=(
                    f"ExecutionRecord '{exec_rec.execution_id}' failed checksum integrity verification"
                ),
                error_code="EXECUTION_CHECKSUM_INVALID",
            )

        # ------------------------------------------------------------------
        # Step 3: Read AttemptRecord & validate integrity/correspondence
        # ------------------------------------------------------------------
        try:
            attempt_rec = self._state_store.get_attempt(exec_rec.attempt_id)
        except StateStoreCorruptionError as exc:
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=f"Corrupted AttemptRecord (invalid checksum): {exc}",
                error_code="ATTEMPT_RECORD_CORRUPTED",
            )
        except StateStoreError as exc:
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=f"State Store error loading attempt '{exec_rec.attempt_id}': {exc}",
                error_code="STATE_STORE_ERROR",
            )

        if attempt_rec is None:
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=f"AttemptRecord '{exec_rec.attempt_id}' not found in State Store",
                error_code="ATTEMPT_NOT_FOUND",
            )

        expected_attempt_checksum = compute_canonical_checksum(_attempt_checksum_payload(attempt_rec))
        if (
            not attempt_rec.checksum_sha256
            or attempt_rec.checksum_sha256 != expected_attempt_checksum
        ):
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=(
                    f"AttemptRecord '{attempt_rec.attempt_id}' failed checksum integrity verification"
                ),
                error_code="ATTEMPT_CHECKSUM_INVALID",
            )

        if attempt_rec.attempt_id != exec_rec.attempt_id or attempt_rec.job_id != exec_rec.job_id:
            return _reject_precheck(
                classification=OrphanClassification.IDENTITY_MISMATCH,
                reason=(
                    f"AttemptRecord ({attempt_rec.attempt_id}, job_id={attempt_rec.job_id}) "
                    f"does not match ExecutionRecord ({exec_rec.attempt_id}, job_id={exec_rec.job_id})"
                ),
                error_code="ATTEMPT_EXECUTION_MISMATCH",
            )

        # ------------------------------------------------------------------
        # Step 4: Validate fencing token across ExecutionRecord, AttemptRecord,
        #         context.identity, and context.expected_fencing_token
        # ------------------------------------------------------------------
        if attempt_rec.fencing_token != exec_rec.fencing_token:
            return _reject_precheck(
                classification=OrphanClassification.IDENTITY_MISMATCH,
                reason=(
                    f"Blocked termination: AttemptRecord fencing_token ({attempt_rec.fencing_token}) "
                    f"!= ExecutionRecord fencing_token ({exec_rec.fencing_token})"
                ),
                error_code="FENCING_TOKEN_MISMATCH",
            )

        if context.identity.fencing_token != exec_rec.fencing_token:
            return _reject_precheck(
                classification=OrphanClassification.IDENTITY_MISMATCH,
                reason=(
                    f"Blocked termination: context fencing_token ({context.identity.fencing_token}) "
                    f"!= persisted fencing_token ({exec_rec.fencing_token})"
                ),
                error_code="FENCING_TOKEN_MISMATCH",
            )

        if (
            context.expected_fencing_token is not None
            and context.expected_fencing_token != exec_rec.fencing_token
        ):
            return _reject_precheck(
                classification=OrphanClassification.IDENTITY_MISMATCH,
                reason=(
                    f"Blocked termination: expected_fencing_token ({context.expected_fencing_token}) "
                    f"!= persisted fencing_token ({exec_rec.fencing_token})"
                ),
                error_code="FENCING_TOKEN_MISMATCH",
            )

        # ------------------------------------------------------------------
        # Step 5: Read ProcessIdentityRecord(s) & validate integrity/checksums
        # ------------------------------------------------------------------
        try:
            persisted_identities = self._state_store.get_process_identities(exec_rec.execution_id)
        except StateStoreCorruptionError as exc:
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=f"Corrupted ProcessIdentityRecord (invalid checksum): {exc}",
                error_code="PROCESS_IDENTITY_CORRUPTED",
            )
        except StateStoreError as exc:
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=f"State Store error loading process identities: {exc}",
                error_code="STATE_STORE_ERROR",
            )

        if not persisted_identities:
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=(
                    f"No persisted ProcessIdentityRecord found for execution '{exec_rec.execution_id}'"
                ),
                error_code="PROCESS_IDENTITY_NOT_FOUND",
            )

        for proc_rec in persisted_identities:
            expected_proc_checksum = compute_canonical_checksum(
                _process_identity_checksum_payload(proc_rec)
            )
            if (
                not proc_rec.checksum_sha256
                or proc_rec.checksum_sha256 != expected_proc_checksum
            ):
                return _reject_precheck(
                    classification=OrphanClassification.INCONCLUSIVE,
                    reason=(
                        f"ProcessIdentityRecord (pid={proc_rec.pid}) for execution "
                        f"'{exec_rec.execution_id}' failed checksum integrity verification"
                    ),
                    error_code="PROCESS_IDENTITY_CHECKSUM_INVALID",
                )

        # ------------------------------------------------------------------
        # Step 6: Validate complete identity & exact correspondence
        # ------------------------------------------------------------------
        ctx_id = context.identity
        exec_mismatches: List[str] = []
        if ctx_id.execution_id != exec_rec.execution_id:
            exec_mismatches.append("execution_id")
        if ctx_id.attempt_id != exec_rec.attempt_id or ctx_id.attempt_id != attempt_rec.attempt_id:
            exec_mismatches.append("attempt_id")
        if ctx_id.job_id != exec_rec.job_id or ctx_id.job_id != attempt_rec.job_id:
            exec_mismatches.append("job_id")
        if ctx_id.tenant_id != exec_rec.tenant_id:
            exec_mismatches.append("tenant_id")
        if ctx_id.channel_id != exec_rec.channel_id:
            exec_mismatches.append("channel_id")
        if ctx_id.worker_id != exec_rec.worker_id:
            exec_mismatches.append("worker_id")
        if ctx_id.fencing_token != exec_rec.fencing_token:
            exec_mismatches.append("fencing_token")
        if ctx_id.browser_user_data_dir != exec_rec.browser_user_data_dir:
            exec_mismatches.append("browser_user_data_dir")

        if exec_mismatches:
            return _reject_precheck(
                classification=OrphanClassification.IDENTITY_MISMATCH,
                reason=(
                    f"Blocked termination: TerminationContext diverges from persisted "
                    f"ExecutionRecord/AttemptRecord on field(s): {', '.join(exec_mismatches)}"
                ),
                error_code="PERSISTED_IDENTITY_MISMATCH",
            )

        matched_proc_identity: Optional[ProcessIdentityRecord] = None
        for proc_rec in persisted_identities:
            if (
                proc_rec.execution_id == exec_rec.execution_id
                and proc_rec.attempt_id == exec_rec.attempt_id
                and proc_rec.pid == ctx_id.pid
                and proc_rec.process_create_time == ctx_id.process_create_time
                and proc_rec.job_object_name == ctx_id.job_object_name
            ):
                matched_proc_identity = proc_rec
                break

        if matched_proc_identity is None:
            return _reject_precheck(
                classification=OrphanClassification.IDENTITY_MISMATCH,
                reason=(
                    f"Blocked termination: process identity (pid={ctx_id.pid}, "
                    f"create_time={ctx_id.process_create_time}, "
                    f"job_object_name={ctx_id.job_object_name!r}) does not match any "
                    f"persisted ProcessIdentityRecord for execution '{exec_rec.execution_id}'"
                ),
                error_code="PROCESS_IDENTITY_MISMATCH",
            )

        persisted_canonical = CanonicalExecutionIdentity(
            execution_id=exec_rec.execution_id,
            attempt_id=exec_rec.attempt_id,
            job_id=exec_rec.job_id,
            tenant_id=exec_rec.tenant_id,
            channel_id=exec_rec.channel_id,
            worker_id=exec_rec.worker_id,
            fencing_token=exec_rec.fencing_token,
            pid=matched_proc_identity.pid,
            process_create_time=matched_proc_identity.process_create_time,
            job_object_name=matched_proc_identity.job_object_name,
            browser_user_data_dir=exec_rec.browser_user_data_dir,
        )

        # ------------------------------------------------------------------
        # Step 7: Validate ExecutionRecord status
        # ------------------------------------------------------------------
        if not isinstance(exec_rec.status, ExecutionStatus) or (
            exec_rec.status not in ALLOWED_TERMINATION_STATUSES
        ):
            status_val = (
                exec_rec.status.value
                if isinstance(exec_rec.status, ExecutionStatus)
                else str(exec_rec.status)
            )
            if exec_rec.status == ExecutionStatus.CLOSED:
                err_code = "EXECUTION_STATUS_CLOSED"
            elif exec_rec.status == ExecutionStatus.INCONCLUSIVE:
                err_code = "EXECUTION_STATUS_INCONCLUSIVE"
            else:
                err_code = "EXECUTION_STATUS_INVALID"
            return _reject_precheck(
                classification=OrphanClassification.INCONCLUSIVE,
                reason=(
                    f"Blocked termination: execution '{exec_rec.execution_id}' has "
                    f"non-terminable status '{status_val}'"
                ),
                error_code=err_code,
            )

        # ------------------------------------------------------------------
        # Step 8: Validate real Job Object handle (never use job_object_name)
        # ------------------------------------------------------------------
        if context.job_handle is None or context.job_handle <= 0:
            missing_job_id = IdentityVerificationResult(
                status=IdentityStatus.UNVERIFIABLE,
                pid=persisted_canonical.pid,
                expected_create_time=persisted_canonical.process_create_time,
                reason="Missing real Job Object handle (job_object_name cannot substitute)",
            )
            return self._persist_and_build_outcome(
                context=context,
                classification=OrphanClassification.INCONCLUSIVE,
                identity_result=missing_job_id,
                verification_result=TerminationVerificationResult.INCONCLUSIVE,
                termination_request_ok=False,
                wait_result=None,
                active_before=None,
                active_after=None,
                pids_before=None,
                pids_after=None,
                pids_truncated=False,
                requested_at_utc=requested_at_utc,
                verified_at_utc=utc_now_iso(),
                exit_code=None,
                reason="Cannot terminate without a real Win32 Job Object handle",
                error_code="MISSING_JOB_HANDLE",
            )

        # ------------------------------------------------------------------
        # Step 8b: Mandatory fail-closed verification of job_handle binding
        #          to the exact authorized Job Object (blocks unregistered,
        #          closed, invalid, unrelated, or outer/nested Job Objects)
        # ------------------------------------------------------------------
        auth_err = self._validate_authorized_job_handle(
            execution_id=exec_rec.execution_id,
            attempt_id=exec_rec.attempt_id,
            fencing_token=exec_rec.fencing_token,
            job_handle=context.job_handle,
            expected_job_handle=context.expected_job_handle,
            persisted_job_object_name=persisted_canonical.job_object_name,
        )
        if auth_err is not None:
            auth_cls, auth_reason, auth_code = auth_err
            return _reject_precheck(
                classification=auth_cls,
                reason=auth_reason,
                error_code=auth_code,
            )

        # ------------------------------------------------------------------
        # Step 9: Execute IdentityValidator.verify() using persisted identity
        # ------------------------------------------------------------------
        id_res = self._identity_validator.verify(
            persisted_canonical,
            job_handle=context.job_handle,
        )

        if id_res.status == IdentityStatus.PID_REUSED:
            return self._persist_and_build_outcome(
                context=context,
                classification=OrphanClassification.IDENTITY_MISMATCH,
                identity_result=id_res,
                verification_result=TerminationVerificationResult.INCONCLUSIVE,
                termination_request_ok=False,
                wait_result=None,
                active_before=None,
                active_after=None,
                pids_before=None,
                pids_after=None,
                pids_truncated=False,
                requested_at_utc=requested_at_utc,
                verified_at_utc=utc_now_iso(),
                exit_code=None,
                reason=f"Blocked termination: PID_REUSED ({id_res.reason})",
                error_code="PID_REUSED",
            )

        if id_res.status == IdentityStatus.NOT_IN_JOB:
            return self._persist_and_build_outcome(
                context=context,
                classification=OrphanClassification.IDENTITY_MISMATCH,
                identity_result=id_res,
                verification_result=TerminationVerificationResult.INCONCLUSIVE,
                termination_request_ok=False,
                wait_result=None,
                active_before=None,
                active_after=None,
                pids_before=None,
                pids_after=None,
                pids_truncated=False,
                requested_at_utc=requested_at_utc,
                verified_at_utc=utc_now_iso(),
                exit_code=None,
                reason=f"Blocked termination: NOT_IN_JOB ({id_res.reason})",
                error_code="NOT_IN_JOB",
            )

        if id_res.status == IdentityStatus.UNVERIFIABLE:
            return self._persist_and_build_outcome(
                context=context,
                classification=OrphanClassification.INCONCLUSIVE,
                identity_result=id_res,
                verification_result=TerminationVerificationResult.INCONCLUSIVE,
                termination_request_ok=False,
                wait_result=None,
                active_before=None,
                active_after=None,
                pids_before=None,
                pids_after=None,
                pids_truncated=False,
                requested_at_utc=requested_at_utc,
                verified_at_utc=utc_now_iso(),
                exit_code=None,
                reason=f"Blocked termination: UNVERIFIABLE ({id_res.reason})",
                error_code="IDENTITY_UNVERIFIABLE",
            )

        if id_res.status == IdentityStatus.PROCESS_NOT_FOUND:
            # Main process already gone; independently verify the Job Object tree
            acct = self._query_accounting(context.job_handle)
            pid_res = self._query_pid_list(context.job_handle)
            if (
                acct is not None
                and acct.active_processes == 0
                and pid_res.ok
                and not pid_res.truncated
                and len(pid_res.pids) == 0
            ):
                return self._persist_and_build_outcome(
                    context=context,
                    classification=OrphanClassification.ALREADY_TERMINATED,
                    identity_result=id_res,
                    verification_result=TerminationVerificationResult.TREE_TERMINATED,
                    termination_request_ok=None,
                    wait_result=None,
                    active_before=0,
                    active_after=0,
                    pids_before=[],
                    pids_after=[],
                    pids_truncated=False,
                    requested_at_utc=requested_at_utc,
                    verified_at_utc=utc_now_iso(),
                    exit_code=None,
                    reason="Process already exited and Job Object confirmed empty (ActiveProcesses=0, PIDs=[])",
                )
            return self._persist_and_build_outcome(
                context=context,
                classification=OrphanClassification.ALREADY_TERMINATED,
                identity_result=id_res,
                verification_result=TerminationVerificationResult.INCONCLUSIVE,
                termination_request_ok=None,
                wait_result=None,
                active_before=acct.active_processes if acct else None,
                active_after=acct.active_processes if acct else None,
                pids_before=pid_res.pids if pid_res.ok else None,
                pids_after=pid_res.pids if pid_res.ok else None,
                pids_truncated=pid_res.truncated,
                requested_at_utc=requested_at_utc,
                verified_at_utc=utc_now_iso(),
                exit_code=None,
                reason="Main process not found, but Job Object tree termination could not be confirmed",
                error_code="TREE_NOT_CONFIRMED_EMPTY",
            )

        # ------------------------------------------------------------------
        # Step 10: Identity is VALID -> capture pre-termination snapshot
        # ------------------------------------------------------------------
        acct_before = self._query_accounting(context.job_handle)
        pids_before_res = self._query_pid_list(context.job_handle)
        active_before = acct_before.active_processes if acct_before is not None else None
        pids_before = list(pids_before_res.pids) if pids_before_res.ok else None

        # Guard against outer/nested Job Objects that contain the watchdog process itself
        if pids_before_res.ok and os.getpid() in pids_before_res.pids:
            return self._persist_and_build_outcome(
                context=context,
                classification=OrphanClassification.IDENTITY_MISMATCH,
                identity_result=id_res,
                verification_result=TerminationVerificationResult.INCONCLUSIVE,
                termination_request_ok=False,
                wait_result=None,
                active_before=active_before,
                active_after=active_before,
                pids_before=pids_before,
                pids_after=pids_before,
                pids_truncated=pids_before_res.truncated,
                requested_at_utc=requested_at_utc,
                verified_at_utc=utc_now_iso(),
                exit_code=None,
                reason=(
                    f"Blocked termination: job_handle ({context.job_handle}) contains "
                    f"the watchdog process itself (pid={os.getpid()}); refusing to terminate "
                    "an outer/shared Job Object"
                ),
                error_code="JOB_HANDLE_CONTAINS_WATCHDOG",
            )

        # ------------------------------------------------------------------
        # Step 11: Re-confirm persisted identity immediately before TerminateJobObject
        # ------------------------------------------------------------------
        recheck_res = self._identity_validator.verify(
            persisted_canonical,
            job_handle=context.job_handle,
        )
        if recheck_res.status != IdentityStatus.VALID:
            return self._persist_and_build_outcome(
                context=context,
                classification=OrphanClassification.IDENTITY_MISMATCH
                if recheck_res.status in (IdentityStatus.PID_REUSED, IdentityStatus.NOT_IN_JOB)
                else OrphanClassification.INCONCLUSIVE,
                identity_result=recheck_res,
                verification_result=TerminationVerificationResult.INCONCLUSIVE,
                termination_request_ok=False,
                wait_result=None,
                active_before=active_before,
                active_after=active_before,
                pids_before=pids_before,
                pids_after=pids_before,
                pids_truncated=pids_before_res.truncated,
                requested_at_utc=requested_at_utc,
                verified_at_utc=utc_now_iso(),
                exit_code=None,
                reason=f"Pre-termination identity re-check failed: {recheck_res.status.value}",
                error_code="IDENTITY_RECHECK_FAILED",
            )

        # 6. Check daemon stop_event before issuing TerminateJobObject
        if self._stop_event is not None and self._stop_event.is_set():
            return self._persist_and_build_outcome(
                context=context,
                classification=OrphanClassification.INCONCLUSIVE,
                identity_result=recheck_res,
                verification_result=TerminationVerificationResult.INCONCLUSIVE,
                termination_request_ok=False,
                wait_result=None,
                active_before=active_before,
                active_after=active_before,
                pids_before=pids_before,
                pids_after=pids_before,
                pids_truncated=pids_before_res.truncated,
                requested_at_utc=requested_at_utc,
                verified_at_utc=utc_now_iso(),
                exit_code=None,
                reason="Termination aborted before request due to daemon shutdown",
                error_code="DAEMON_INTERRUPTED",
            )

        # 6b. Repeat mandatory Job Object handle authorization check IMMEDIATELY
        #     before TerminateJobObject (prevents TOCTOU unregistration/close/swap)
        recheck_auth_err = self._validate_authorized_job_handle(
            execution_id=exec_rec.execution_id,
            attempt_id=exec_rec.attempt_id,
            fencing_token=exec_rec.fencing_token,
            job_handle=context.job_handle,
            expected_job_handle=context.expected_job_handle,
            persisted_job_object_name=persisted_canonical.job_object_name,
        )
        if recheck_auth_err is not None:
            auth_cls, auth_reason, auth_code = recheck_auth_err
            return _reject_precheck(
                classification=auth_cls,
                reason=f"Pre-termination handle authorization re-check failed: {auth_reason}",
                error_code=auth_code,
            )

        # 7. Execute TerminateJobObject on the real Job Object handle
        try:
            term_ok = bool(self._win32.terminate_job_object(context.job_handle, exit_code))
        except Exception as exc:
            return self._persist_and_build_outcome(
                context=context,
                classification=OrphanClassification.SAFE_TO_TERMINATE,
                identity_result=recheck_res,
                verification_result=TerminationVerificationResult.TERMINATION_FAILED,
                termination_request_ok=False,
                wait_result=None,
                active_before=active_before,
                active_after=None,
                pids_before=pids_before,
                pids_after=None,
                pids_truncated=False,
                requested_at_utc=requested_at_utc,
                verified_at_utc=utc_now_iso(),
                exit_code=None,
                reason=f"TerminateJobObject raised exception: {exc}",
                error_code="TERMINATE_JOB_OBJECT_EXCEPTION",
            )

        if not term_ok:
            err = self._win32.get_last_error()
            return self._persist_and_build_outcome(
                context=context,
                classification=OrphanClassification.SAFE_TO_TERMINATE,
                identity_result=recheck_res,
                verification_result=TerminationVerificationResult.TERMINATION_FAILED,
                termination_request_ok=False,
                wait_result=None,
                active_before=active_before,
                active_after=None,
                pids_before=pids_before,
                pids_after=None,
                pids_truncated=False,
                requested_at_utc=requested_at_utc,
                verified_at_utc=utc_now_iso(),
                exit_code=None,
                reason=f"TerminateJobObject returned False (win32_err={err})",
                error_code="TERMINATE_JOB_OBJECT_FAILED",
            )

        # 8. Independent post-termination verification (Section 3.4)
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        wait_result: Optional[int] = None
        observed_exit_code: Optional[int] = None
        last_acct: Optional[BasicAccountingInfo] = None
        last_pids: Optional[ProcessIdListResult] = None

        while True:
            if self._stop_event is not None and self._stop_event.is_set():
                return self._persist_and_build_outcome(
                    context=context,
                    classification=OrphanClassification.SAFE_TO_TERMINATE,
                    identity_result=recheck_res,
                    verification_result=TerminationVerificationResult.INCONCLUSIVE,
                    termination_request_ok=True,
                    wait_result=wait_result,
                    active_before=active_before,
                    active_after=last_acct.active_processes if last_acct else None,
                    pids_before=pids_before,
                    pids_after=list(last_pids.pids) if last_pids and last_pids.ok else None,
                    pids_truncated=last_pids.truncated if last_pids else False,
                    requested_at_utc=requested_at_utc,
                    verified_at_utc=utc_now_iso(),
                    exit_code=observed_exit_code,
                    reason="Daemon interrupted during post-termination verification; classified INCONCLUSIVE",
                    error_code="DAEMON_INTERRUPTED",
                )

            if context.process_handle is not None and wait_result != WAIT_OBJECT_0:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                slice_ms = min(self._poll_interval_ms, remaining_ms)
                try:
                    wait_result = self._win32.wait_for_single_object(
                        context.process_handle, slice_ms
                    )
                except Exception as exc:
                    return self._persist_and_build_outcome(
                        context=context,
                        classification=OrphanClassification.SAFE_TO_TERMINATE,
                        identity_result=recheck_res,
                        verification_result=TerminationVerificationResult.INCONCLUSIVE,
                        termination_request_ok=True,
                        wait_result=WAIT_FAILED,
                        active_before=active_before,
                        active_after=last_acct.active_processes if last_acct else None,
                        pids_before=pids_before,
                        pids_after=list(last_pids.pids) if last_pids and last_pids.ok else None,
                        pids_truncated=last_pids.truncated if last_pids else False,
                        requested_at_utc=requested_at_utc,
                        verified_at_utc=utc_now_iso(),
                        exit_code=observed_exit_code,
                        reason=f"WaitForSingleObject raised exception: {exc}",
                        error_code="WAIT_FOR_SINGLE_OBJECT_FAILED",
                    )

                if wait_result == WAIT_FAILED:
                    return self._persist_and_build_outcome(
                        context=context,
                        classification=OrphanClassification.SAFE_TO_TERMINATE,
                        identity_result=recheck_res,
                        verification_result=TerminationVerificationResult.INCONCLUSIVE,
                        termination_request_ok=True,
                        wait_result=WAIT_FAILED,
                        active_before=active_before,
                        active_after=last_acct.active_processes if last_acct else None,
                        pids_before=pids_before,
                        pids_after=list(last_pids.pids) if last_pids and last_pids.ok else None,
                        pids_truncated=last_pids.truncated if last_pids else False,
                        requested_at_utc=requested_at_utc,
                        verified_at_utc=utc_now_iso(),
                        exit_code=observed_exit_code,
                        reason="WaitForSingleObject returned WAIT_FAILED",
                        error_code="WAIT_FOR_SINGLE_OBJECT_FAILED",
                    )

                if wait_result == WAIT_OBJECT_0:
                    try:
                        ok_exit, code = self._win32.get_exit_code_process(context.process_handle)
                        if ok_exit and code != STILL_ACTIVE:
                            observed_exit_code = int(code)
                    except Exception:
                        pass

            acct = self._query_accounting(context.job_handle)
            if acct is None:
                return self._persist_and_build_outcome(
                    context=context,
                    classification=OrphanClassification.SAFE_TO_TERMINATE,
                    identity_result=recheck_res,
                    verification_result=TerminationVerificationResult.INCONCLUSIVE,
                    termination_request_ok=True,
                    wait_result=wait_result,
                    active_before=active_before,
                    active_after=None,
                    pids_before=pids_before,
                    pids_after=None,
                    pids_truncated=False,
                    requested_at_utc=requested_at_utc,
                    verified_at_utc=utc_now_iso(),
                    exit_code=observed_exit_code,
                    reason="QueryInformationJobObject(BasicAccountingInformation) failed after TerminateJobObject",
                    error_code="ACCOUNTING_QUERY_FAILED",
                )
            last_acct = acct

            pid_res = self._query_pid_list(context.job_handle)
            last_pids = pid_res
            if pid_res.truncated or not pid_res.ok:
                return self._persist_and_build_outcome(
                    context=context,
                    classification=OrphanClassification.SAFE_TO_TERMINATE,
                    identity_result=recheck_res,
                    verification_result=TerminationVerificationResult.INCONCLUSIVE,
                    termination_request_ok=True,
                    wait_result=wait_result,
                    active_before=active_before,
                    active_after=acct.active_processes,
                    pids_before=pids_before,
                    pids_after=list(pid_res.pids),
                    pids_truncated=pid_res.truncated,
                    requested_at_utc=requested_at_utc,
                    verified_at_utc=utc_now_iso(),
                    exit_code=observed_exit_code,
                    reason=pid_res.error_message or "PID list query failed or was truncated",
                    error_code="PID_LIST_TRUNCATED" if pid_res.truncated else "PID_LIST_QUERY_FAILED",
                )

            if acct.active_processes == 0 and len(pid_res.pids) == 0:
                return self._persist_and_build_outcome(
                    context=context,
                    classification=OrphanClassification.SAFE_TO_TERMINATE,
                    identity_result=recheck_res,
                    verification_result=TerminationVerificationResult.TREE_TERMINATED,
                    termination_request_ok=True,
                    wait_result=wait_result,
                    active_before=active_before,
                    active_after=0,
                    pids_before=pids_before,
                    pids_after=[],
                    pids_truncated=False,
                    requested_at_utc=requested_at_utc,
                    verified_at_utc=utc_now_iso(),
                    exit_code=observed_exit_code,
                    reason="Confirmed full process tree termination (ActiveProcesses=0, non-truncated PIDs=[])",
                )

            if time.monotonic() >= deadline:
                return self._persist_and_build_outcome(
                    context=context,
                    classification=OrphanClassification.SAFE_TO_TERMINATE,
                    identity_result=recheck_res,
                    verification_result=TerminationVerificationResult.TERMINATION_TIMEOUT,
                    termination_request_ok=True,
                    wait_result=wait_result,
                    active_before=active_before,
                    active_after=last_acct.active_processes,
                    pids_before=pids_before,
                    pids_after=list(last_pids.pids),
                    pids_truncated=last_pids.truncated,
                    requested_at_utc=requested_at_utc,
                    verified_at_utc=utc_now_iso(),
                    exit_code=observed_exit_code,
                    reason=(
                        f"Termination verification timed out after {timeout_ms}ms "
                        f"(ActiveProcesses={last_acct.active_processes}, PIDs={last_pids.pids})"
                    ),
                    error_code="TERMINATION_TIMEOUT",
                )

            time.sleep(self._poll_interval_ms / 1000.0)
