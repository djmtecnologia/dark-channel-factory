"""Persistent termination evidence and durable Outbox integration (Sprint 2.7 - Task 6).

Builds canonical, checksum-verified termination evidence records and persists
them durably via StateStore and OutboxStore. When OutboxStore shares the same
StateStore instance, evidence persistence and Outbox event enqueueing execute
atomically inside a single SQLite transaction.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from desktop_worker.watchdog.outbox import (
    OutboxError,
    OutboxEventRecord,
    OutboxStore,
)
from desktop_worker.watchdog.state_store import (
    SCHEMA_VERSION,
    StateStore,
    StateStoreConflictError,
    StateStoreError,
    StateStorePersistenceError,
    TerminationEvidenceRecord,
    _evidence_checksum_payload,
    compute_canonical_checksum,
)

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class TerminationVerificationResult(str, Enum):
    """Independent verification outcome of a safe termination operation."""

    TREE_TERMINATED = "TREE_TERMINATED"
    EXECUTION_ISOLATED = "EXECUTION_ISOLATED"
    TERMINATION_REQUESTED = "TERMINATION_REQUESTED"
    TERMINATION_TIMEOUT = "TERMINATION_TIMEOUT"
    TERMINATION_FAILED = "TERMINATION_FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


class TerminationOutboxEventType(str, Enum):
    """Durable Outbox event types emitted for termination operations."""

    TERMINATION_REQUESTED = "TERMINATION_REQUESTED"
    TERMINATION_CONFIRMED = "TERMINATION_CONFIRMED"
    TERMINATION_INCONCLUSIVE = "TERMINATION_INCONCLUSIVE"
    TERMINATION_FAILED = "TERMINATION_FAILED"


def map_verification_to_outbox_event_type(
    verification_result: TerminationVerificationResult,
) -> TerminationOutboxEventType:
    """Map a TerminationVerificationResult to its durable Outbox event type."""
    if verification_result == TerminationVerificationResult.TREE_TERMINATED:
        return TerminationOutboxEventType.TERMINATION_CONFIRMED
    if verification_result == TerminationVerificationResult.TERMINATION_REQUESTED:
        return TerminationOutboxEventType.TERMINATION_REQUESTED
    if verification_result == TerminationVerificationResult.TERMINATION_FAILED:
        return TerminationOutboxEventType.TERMINATION_FAILED
    return TerminationOutboxEventType.TERMINATION_INCONCLUSIVE


@dataclass(frozen=True)
class TerminationEvidencePayload:
    """Structured evidence payload capturing all minimum verification fields."""

    execution_id: str
    attempt_id: str
    job_id: str
    worker_id: str
    fencing_token: int
    pid: int
    process_create_time: int
    job_object_name: str
    identity_validation_status: str
    identity_validation_reason: str
    termination_request_ok: Optional[bool]
    wait_result: Optional[int]
    active_processes_before: Optional[int]
    active_processes_after: Optional[int]
    pids_before: Optional[List[int]]
    pids_after: Optional[List[int]]
    pids_truncated: bool
    termination_requested_at_utc: str
    verified_at_utc: str
    exit_code: Optional[int]
    termination_reason: str
    watchdog_version: str
    verification_result: TerminationVerificationResult
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a canonical JSON-compatible dictionary."""
        return {
            "execution_id": self.execution_id,
            "attempt_id": self.attempt_id,
            "job_id": self.job_id,
            "worker_id": self.worker_id,
            "fencing_token": self.fencing_token,
            "pid": self.pid,
            "process_create_time": self.process_create_time,
            "job_object_name": self.job_object_name,
            "identity_validation_status": self.identity_validation_status,
            "identity_validation_reason": self.identity_validation_reason,
            "termination_request_ok": self.termination_request_ok,
            "wait_result": self.wait_result,
            "active_processes_before": self.active_processes_before,
            "active_processes_after": self.active_processes_after,
            "pids_before": list(self.pids_before) if self.pids_before is not None else None,
            "pids_after": list(self.pids_after) if self.pids_after is not None else None,
            "pids_truncated": self.pids_truncated,
            "termination_requested_at_utc": self.termination_requested_at_utc,
            "verified_at_utc": self.verified_at_utc,
            "exit_code": self.exit_code,
            "termination_reason": self.termination_reason,
            "watchdog_version": self.watchdog_version,
            "verification_result": self.verification_result.value,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }

    def to_canonical_json(self) -> str:
        """Serialize to deterministic canonical JSON string."""
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )


@dataclass(frozen=True)
class EvidencePersistenceResult:
    """Result of persisting termination evidence and its Outbox event."""

    persisted: bool
    evidence_record: Optional[TerminationEvidenceRecord] = None
    outbox_event: Optional[OutboxEventRecord] = None
    idempotent_duplicate: bool = False
    error: Optional[str] = None


class TerminationEvidenceRecorder:
    """Persists termination evidence and enqueues durable Outbox events."""

    def __init__(
        self,
        state_store: StateStore,
        outbox_store: Optional[OutboxStore] = None,
    ) -> None:
        self._state_store = state_store
        self._outbox_store = outbox_store

    @staticmethod
    def derive_stable_event_id(
        payload: TerminationEvidencePayload,
        *,
        evidence_source: str = "WATCHDOG_TERMINATION_ENGINE",
    ) -> str:
        """Derive a deterministic, stable event_id for idempotent termination evidence."""
        key = {
            "execution_id": payload.execution_id,
            "attempt_id": payload.attempt_id,
            "job_id": payload.job_id,
            "worker_id": payload.worker_id,
            "fencing_token": payload.fencing_token,
            "pid": payload.pid,
            "process_create_time": payload.process_create_time,
            "job_object_name": payload.job_object_name,
            "termination_reason": payload.termination_reason,
            "verification_result": payload.verification_result.value,
            "evidence_source": evidence_source,
        }
        digest = compute_canonical_checksum(key)[:32]
        return f"term-{digest}"

    def build_evidence_record(
        self,
        payload: TerminationEvidencePayload,
        *,
        event_id: Optional[str] = None,
        evidence_source: str = "WATCHDOG_TERMINATION_ENGINE",
    ) -> TerminationEvidenceRecord:
        """Construct a TerminationEvidenceRecord with canonical JSON and SHA-256 checksum."""
        stable_event_id = (event_id or "").strip() or self.derive_stable_event_id(
            payload,
            evidence_source=evidence_source,
        )
        payload_json = payload.to_canonical_json()
        rec = TerminationEvidenceRecord(
            event_id=stable_event_id,
            attempt_id=payload.attempt_id,
            execution_id=payload.execution_id,
            job_id=payload.job_id,
            worker_id=payload.worker_id,
            fencing_token=payload.fencing_token,
            pid=payload.pid,
            process_create_time=payload.process_create_time,
            job_object_name=payload.job_object_name,
            verification_result=payload.verification_result.value,
            termination_reason=payload.termination_reason,
            evidence_source=evidence_source,
            watchdog_version=payload.watchdog_version,
            termination_requested_at_utc=payload.termination_requested_at_utc,
            verified_at_utc=payload.verified_at_utc,
            payload_json=payload_json,
            schema_version=SCHEMA_VERSION,
        )
        checksum = compute_canonical_checksum(_evidence_checksum_payload(rec))
        return TerminationEvidenceRecord(**{**rec.__dict__, "checksum_sha256": checksum})

    def persist(
        self,
        evidence: TerminationEvidenceRecord,
        *,
        outbox_event_type: Optional[TerminationOutboxEventType] = None,
    ) -> EvidencePersistenceResult:
        """Persist evidence durably and enqueue Outbox event (atomically when possible).

        Idempotent by ``evidence.event_id``: if an identical evidence record
        already exists, it is preserved without overwriting. Conflicting
        content for the same ``event_id`` fails safely.
        """
        event_type = outbox_event_type or map_verification_to_outbox_event_type(
            TerminationVerificationResult(evidence.verification_result)
        )

        # Atomic single-transaction path when OutboxStore shares the same StateStore
        can_use_shared_tx = (
            self._outbox_store is not None
            and getattr(self._outbox_store, "_store", None) is self._state_store
            and hasattr(self._state_store, "_transaction")
            and hasattr(self._state_store, "_validate_and_build_execution")
            and hasattr(self._state_store, "_validate_and_build_identity")
            and hasattr(self._state_store, "_validate_and_build_evidence")
        )

        if can_use_shared_tx:
            return self._persist_atomic_sqlite(evidence, event_type)

        return self._persist_via_public_api(evidence, event_type)

    def _validate_evidence_fields(self, evidence: TerminationEvidenceRecord) -> TerminationEvidenceRecord:
        required_fields = {
            "event_id": evidence.event_id,
            "attempt_id": evidence.attempt_id,
            "execution_id": evidence.execution_id,
            "job_id": evidence.job_id,
            "worker_id": evidence.worker_id,
            "job_object_name": evidence.job_object_name,
            "verification_result": evidence.verification_result,
            "termination_reason": evidence.termination_reason,
            "evidence_source": evidence.evidence_source,
            "watchdog_version": evidence.watchdog_version,
        }
        missing = [k for k, v in required_fields.items() if not v or not str(v).strip()]
        if missing:
            raise StateStorePersistenceError(
                f"Missing required evidence fields: {', '.join(missing)}"
            )
        try:
            parsed_payload = json.loads(evidence.payload_json)
            if not isinstance(parsed_payload, dict):
                raise StateStorePersistenceError("evidence.payload_json must be a JSON object")
        except json.JSONDecodeError as exc:
            raise StateStorePersistenceError(f"Invalid evidence.payload_json: {exc}") from exc

        rec = TerminationEvidenceRecord(
            **{**evidence.__dict__, "schema_version": SCHEMA_VERSION}
        )
        checksum = compute_canonical_checksum(_evidence_checksum_payload(rec))
        return TerminationEvidenceRecord(**{**rec.__dict__, "checksum_sha256": checksum})

    @staticmethod
    def _evidence_matches(a: TerminationEvidenceRecord, b: TerminationEvidenceRecord) -> bool:
        """Return True if ``b`` is an idempotent replay of ``a`` (ignoring volatile timestamps).

        Preserves strict conflict detection: any divergence in identity,
        fencing_token, verification_result, termination_reason, watchdog_version,
        evidence_source, error_code, or semantic payload fields returns False.
        """
        if not (
            a.event_id == b.event_id
            and a.attempt_id == b.attempt_id
            and a.execution_id == b.execution_id
            and a.job_id == b.job_id
            and a.worker_id == b.worker_id
            and a.fencing_token == b.fencing_token
            and a.pid == b.pid
            and a.process_create_time == b.process_create_time
            and a.job_object_name == b.job_object_name
            and a.verification_result == b.verification_result
            and a.termination_reason == b.termination_reason
            and a.evidence_source == b.evidence_source
            and a.watchdog_version == b.watchdog_version
        ):
            return False

        try:
            a_payload = json.loads(a.payload_json)
            b_payload = json.loads(b.payload_json)
        except (TypeError, ValueError):
            return a.payload_json == b.payload_json

        if not isinstance(a_payload, dict) or not isinstance(b_payload, dict):
            return a_payload == b_payload

        volatile_timestamp_keys = {"termination_requested_at_utc", "verified_at_utc"}
        a_semantic = {k: v for k, v in a_payload.items() if k not in volatile_timestamp_keys}
        b_semantic = {k: v for k, v in b_payload.items() if k not in volatile_timestamp_keys}
        if a_semantic == b_semantic:
            return True

        # Idempotent post-termination replay: the first call terminated the tree
        # (TREE_TERMINATED), and a subsequent call (e.g. after restart) observes
        # an already-empty Job Object (ActiveProcesses=0, PIDs=[]), whether the
        # OS reports PROCESS_NOT_FOUND or VALID for a still-reaping PID.
        if (
            a.verification_result == TerminationVerificationResult.TREE_TERMINATED.value
            and b.verification_result == TerminationVerificationResult.TREE_TERMINATED.value
            and b_semantic.get("identity_validation_status") in ("PROCESS_NOT_FOUND", "VALID")
            and b_semantic.get("active_processes_before") == 0
            and b_semantic.get("active_processes_after") == 0
            and b_semantic.get("pids_after") == []
            and b_semantic.get("pids_truncated") is False
            and a_semantic.get("error_code") == b_semantic.get("error_code")
            and a_semantic.get("termination_reason") == b_semantic.get("termination_reason")
        ):
            return True

        return False

    def _build_outbox_payload(self, rec: TerminationEvidenceRecord) -> Dict[str, Any]:
        return {
            "event_id": rec.event_id,
            "execution_id": rec.execution_id,
            "attempt_id": rec.attempt_id,
            "job_id": rec.job_id,
            "worker_id": rec.worker_id,
            "fencing_token": rec.fencing_token,
            "pid": rec.pid,
            "process_create_time": rec.process_create_time,
            "job_object_name": rec.job_object_name,
            "verification_result": rec.verification_result,
            "termination_reason": rec.termination_reason,
            "evidence_source": rec.evidence_source,
            "watchdog_version": rec.watchdog_version,
            "termination_requested_at_utc": rec.termination_requested_at_utc,
            "verified_at_utc": rec.verified_at_utc,
            "evidence_checksum_sha256": rec.checksum_sha256,
            "details": json.loads(rec.payload_json),
        }

    def _persist_atomic_sqlite(
        self,
        evidence: TerminationEvidenceRecord,
        event_type: TerminationOutboxEventType,
    ) -> EvidencePersistenceResult:
        """Persist evidence and Outbox event atomically in one SQLite transaction."""
        assert self._outbox_store is not None
        try:
            rec = self._validate_evidence_fields(evidence)
            idempotent_dup = False
            with self._state_store._transaction() as conn:
                existing_row = conn.execute(
                    "SELECT * FROM termination_evidence WHERE event_id = ?;",
                    (rec.event_id,),
                ).fetchone()
                if existing_row is not None:
                    existing = self._state_store._validate_and_build_evidence(existing_row)
                    if self._evidence_matches(existing, rec):
                        rec = existing
                        idempotent_dup = True
                    else:
                        raise StateStoreConflictError(
                            f"Duplicate evidence event_id '{rec.event_id}' with conflicting content"
                        )
                else:
                    exec_row = conn.execute(
                        "SELECT * FROM upload_execution WHERE execution_id = ?;",
                        (rec.execution_id,),
                    ).fetchone()
                    if exec_row is None:
                        raise StateStorePersistenceError(
                            f"Referenced execution '{rec.execution_id}' does not exist"
                        )
                    exec_rec = self._state_store._validate_and_build_execution(exec_row)
                    if rec.attempt_id != exec_rec.attempt_id:
                        raise StateStoreConflictError(
                            f"Evidence attempt_id '{rec.attempt_id}' != execution attempt_id '{exec_rec.attempt_id}'"
                        )
                    if rec.job_id != exec_rec.job_id:
                        raise StateStoreConflictError(
                            f"Evidence job_id '{rec.job_id}' != execution job_id '{exec_rec.job_id}'"
                        )
                    if rec.worker_id != exec_rec.worker_id:
                        raise StateStoreConflictError(
                            f"Evidence worker_id '{rec.worker_id}' != execution worker_id '{exec_rec.worker_id}'"
                        )
                    if rec.fencing_token != exec_rec.fencing_token:
                        raise StateStoreConflictError(
                            f"Evidence fencing_token {rec.fencing_token} != execution fencing_token {exec_rec.fencing_token}"
                        )

                    id_rows = conn.execute(
                        "SELECT * FROM process_identity WHERE execution_id = ?;",
                        (rec.execution_id,),
                    ).fetchall()
                    if not id_rows:
                        raise StateStoreConflictError(
                            f"No persisted process_identity found for execution '{rec.execution_id}'"
                        )
                    valid_identities = [
                        self._state_store._validate_and_build_identity(r) for r in id_rows
                    ]
                    if not any(
                        i.pid == rec.pid
                        and i.process_create_time == rec.process_create_time
                        and i.execution_id == rec.execution_id
                        and i.attempt_id == rec.attempt_id
                        and i.job_object_name == rec.job_object_name
                        for i in valid_identities
                    ):
                        raise StateStoreConflictError(
                            f"Evidence (execution_id={rec.execution_id}, attempt_id={rec.attempt_id}, "
                            f"pid={rec.pid}, create_time={rec.process_create_time}, "
                            f"job_object_name={rec.job_object_name}) does not match any persisted identity"
                        )

                    conn.execute(
                        """
                        INSERT INTO termination_evidence (
                            event_id, attempt_id, execution_id, job_id, worker_id,
                            fencing_token, pid, process_create_time, job_object_name,
                            verification_result, termination_reason, evidence_source,
                            watchdog_version, termination_requested_at_utc, verified_at_utc,
                            payload_json, schema_version, checksum_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            rec.event_id,
                            rec.attempt_id,
                            rec.execution_id,
                            rec.job_id,
                            rec.worker_id,
                            rec.fencing_token,
                            rec.pid,
                            rec.process_create_time,
                            rec.job_object_name,
                            rec.verification_result,
                            rec.termination_reason,
                            rec.evidence_source,
                            rec.watchdog_version,
                            rec.termination_requested_at_utc,
                            rec.verified_at_utc,
                            rec.payload_json,
                            rec.schema_version,
                            rec.checksum_sha256,
                        ),
                    )

                outbox_rec = self._outbox_store.enqueue_event(
                    event_id=rec.event_id,
                    event_type=event_type.value,
                    execution_id=rec.execution_id,
                    attempt_id=rec.attempt_id,
                    job_id=rec.job_id,
                    worker_id=rec.worker_id,
                    fencing_token=rec.fencing_token,
                    payload=self._build_outbox_payload(rec),
                    conn=conn,
                )

            return EvidencePersistenceResult(
                persisted=True,
                evidence_record=rec,
                outbox_event=outbox_rec,
                idempotent_duplicate=idempotent_dup,
            )
        except (StateStoreError, OutboxError, sqlite3.Error, ValueError) as exc:
            logger.error(
                "Atomic termination evidence + outbox persistence failed: %s",
                exc,
                extra={"event_id": evidence.event_id, "execution_id": evidence.execution_id},
            )
            return EvidencePersistenceResult(
                persisted=False,
                error=str(exc),
            )

    def _persist_via_public_api(
        self,
        evidence: TerminationEvidenceRecord,
        event_type: TerminationOutboxEventType,
    ) -> EvidencePersistenceResult:
        """Persist evidence via StateStore.save_evidence() and optionally OutboxStore."""
        try:
            rec = self._validate_evidence_fields(evidence)
            idempotent_dup = False

            # Check idempotency via read API when available
            matched_existing: Optional[TerminationEvidenceRecord] = None
            if hasattr(self._state_store, "get_evidence"):
                try:
                    matched_existing = self._state_store.get_evidence(rec.event_id)
                except Exception:
                    matched_existing = None
            if matched_existing is not None:
                if self._evidence_matches(matched_existing, rec):
                    saved_rec = matched_existing
                    idempotent_dup = True
                else:
                    raise StateStoreConflictError(
                        f"Duplicate evidence event_id '{rec.event_id}' with conflicting content"
                    )
            else:
                saved_rec = self._state_store.save_evidence(rec)

            outbox_rec: Optional[OutboxEventRecord] = None
            if self._outbox_store is not None:
                outbox_rec = self._outbox_store.enqueue_event(
                    event_id=saved_rec.event_id,
                    event_type=event_type.value,
                    execution_id=saved_rec.execution_id,
                    attempt_id=saved_rec.attempt_id,
                    job_id=saved_rec.job_id,
                    worker_id=saved_rec.worker_id,
                    fencing_token=saved_rec.fencing_token,
                    payload=self._build_outbox_payload(saved_rec),
                )

            return EvidencePersistenceResult(
                persisted=True,
                evidence_record=saved_rec,
                outbox_event=outbox_rec,
                idempotent_duplicate=idempotent_dup,
            )
        except Exception as exc:
            logger.error(
                "Termination evidence persistence failed: %s",
                exc,
                extra={"event_id": evidence.event_id, "execution_id": evidence.execution_id},
            )
            return EvidencePersistenceResult(
                persisted=False,
                error=str(exc),
            )
