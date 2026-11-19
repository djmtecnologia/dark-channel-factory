"""Durable local SQLite State Store for the Local Watchdog (Sprint 2.7 - Task 3).

Provides transactional, checksum-verified persistence for upload executions,
upload attempts, canonical process identities, termination evidence, and
watchdog checkpoints using SQLite in WAL mode with synchronous=FULL.

This module is strictly a persistence layer: it does not launch or terminate
processes, perform YouTube reconciliation, communicate with Cloud Hub, or
implement Outbox delivery (reserved for Task 4).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

from desktop_worker.watchdog.identity import CanonicalExecutionIdentity

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Enums & Exceptions
# ---------------------------------------------------------------------------


class ExecutionStatus(str, Enum):
    """Lifecycle status of a local upload execution."""

    RUNNING = "RUNNING"
    TERMINATING = "TERMINATING"
    CLOSED = "CLOSED"
    INCONCLUSIVE = "INCONCLUSIVE"


class AttemptHubStatus(str, Enum):
    """Cloud Hub attempt status mirrored locally."""

    INTENT = "INTENT"
    IN_PROGRESS = "IN_PROGRESS"
    UNKNOWN = "UNKNOWN"
    TERMINATED = "TERMINATED"
    CONFIRMED = "CONFIRMED"
    INCONCLUSIVE = "INCONCLUSIVE"


class IntegrityStatus(str, Enum):
    """Integrity verification outcome for a persisted record."""

    VALID = "VALID"
    INCONCLUSIVE = "INCONCLUSIVE"


class StateStoreError(RuntimeError):
    """Base exception for all State Store failures."""


class StateStorePersistenceError(StateStoreError):
    """Raised when a durable write/read operation fails (disk/SQLite error)."""


class StateStoreSchemaError(StateStoreError):
    """Raised when the SQLite database schema or version is incompatible."""


class StateStoreCorruptionError(StateStoreError):
    """Raised when persisted data fails checksum or structural validation."""


class StateStoreConflictError(StateStoreError):
    """Raised when a stale fencing_token or conflicting identity attempts an update."""


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionRecord:
    """Persisted upload execution record."""

    execution_id: str
    attempt_id: str
    job_id: str
    tenant_id: str
    channel_id: str
    worker_id: str
    fencing_token: int
    browser_user_data_dir: str
    boot_time_utc: str
    status: ExecutionStatus
    created_at_utc: str
    updated_at_utc: str
    schema_version: int = SCHEMA_VERSION
    checksum_sha256: str = ""


@dataclass(frozen=True)
class AttemptRecord:
    """Persisted upload attempt record."""

    attempt_id: str
    job_id: str
    fencing_token: int
    hub_status: AttemptHubStatus
    last_heartbeat_at_utc: Optional[str]
    created_at_utc: str
    updated_at_utc: str
    schema_version: int = SCHEMA_VERSION
    checksum_sha256: str = ""


@dataclass(frozen=True)
class ProcessIdentityRecord:
    """Persisted canonical process identity record."""

    execution_id: str
    attempt_id: str
    pid: int
    process_create_time: int  # Win32 FILETIME (100-ns intervals)
    job_object_name: str
    role: str
    recorded_at_utc: str
    record_id: Optional[int] = None
    schema_version: int = SCHEMA_VERSION
    checksum_sha256: str = ""


@dataclass(frozen=True)
class TerminationEvidenceRecord:
    """Persisted termination evidence record."""

    event_id: str
    attempt_id: str
    execution_id: str
    job_id: str
    worker_id: str
    fencing_token: int
    pid: int
    process_create_time: int
    job_object_name: str
    verification_result: str
    termination_reason: str
    evidence_source: str
    watchdog_version: str
    termination_requested_at_utc: str
    verified_at_utc: str
    payload_json: str
    schema_version: int = SCHEMA_VERSION
    checksum_sha256: str = ""


@dataclass(frozen=True)
class CheckpointRecord:
    """Persisted watchdog checkpoint record."""

    worker_id: str
    last_boot_time_utc: str
    last_scan_at_utc: str
    watchdog_pid: int
    watchdog_version: str
    schema_version: int = SCHEMA_VERSION
    checksum_sha256: str = ""


@dataclass(frozen=True)
class IntegrityVerificationReport:
    """Result of verifying a persisted record or database."""

    status: IntegrityStatus
    table: str
    record_key: str
    reason: str = ""
    raw_diagnostic: Optional[Dict[str, Any]] = None

    @property
    def is_valid(self) -> bool:
        return self.status == IntegrityStatus.VALID


# ---------------------------------------------------------------------------
# Checksum Helpers
# ---------------------------------------------------------------------------


def compute_canonical_checksum(payload: Dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hex digest for a canonical dictionary."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _execution_checksum_payload(rec: ExecutionRecord) -> Dict[str, Any]:
    return {
        "schema_version": rec.schema_version,
        "execution_id": rec.execution_id,
        "attempt_id": rec.attempt_id,
        "job_id": rec.job_id,
        "tenant_id": rec.tenant_id,
        "channel_id": rec.channel_id,
        "worker_id": rec.worker_id,
        "fencing_token": rec.fencing_token,
        "browser_user_data_dir": rec.browser_user_data_dir,
        "boot_time_utc": rec.boot_time_utc,
        "status": rec.status.value if isinstance(rec.status, ExecutionStatus) else str(rec.status),
        "created_at_utc": rec.created_at_utc,
        "updated_at_utc": rec.updated_at_utc,
    }


def _attempt_checksum_payload(rec: AttemptRecord) -> Dict[str, Any]:
    return {
        "schema_version": rec.schema_version,
        "attempt_id": rec.attempt_id,
        "job_id": rec.job_id,
        "fencing_token": rec.fencing_token,
        "hub_status": rec.hub_status.value if isinstance(rec.hub_status, AttemptHubStatus) else str(rec.hub_status),
        "last_heartbeat_at_utc": rec.last_heartbeat_at_utc,
        "created_at_utc": rec.created_at_utc,
        "updated_at_utc": rec.updated_at_utc,
    }


def _process_identity_checksum_payload(rec: ProcessIdentityRecord) -> Dict[str, Any]:
    return {
        "schema_version": rec.schema_version,
        "execution_id": rec.execution_id,
        "attempt_id": rec.attempt_id,
        "pid": rec.pid,
        "process_create_time": rec.process_create_time,
        "job_object_name": rec.job_object_name,
        "role": rec.role,
        "recorded_at_utc": rec.recorded_at_utc,
    }


def _evidence_checksum_payload(rec: TerminationEvidenceRecord) -> Dict[str, Any]:
    return {
        "schema_version": rec.schema_version,
        "event_id": rec.event_id,
        "attempt_id": rec.attempt_id,
        "execution_id": rec.execution_id,
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
        "payload_json": rec.payload_json,
    }


def _checkpoint_checksum_payload(rec: CheckpointRecord) -> Dict[str, Any]:
    return {
        "schema_version": rec.schema_version,
        "worker_id": rec.worker_id,
        "last_boot_time_utc": rec.last_boot_time_utc,
        "last_scan_at_utc": rec.last_scan_at_utc,
        "watchdog_pid": rec.watchdog_pid,
        "watchdog_version": rec.watchdog_version,
    }


# ---------------------------------------------------------------------------
# SQLite Schema
# ---------------------------------------------------------------------------

_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schema_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS upload_execution (
        execution_id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL,
        job_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        worker_id TEXT NOT NULL,
        fencing_token INTEGER NOT NULL CHECK (fencing_token >= 0),
        browser_user_data_dir TEXT NOT NULL,
        boot_time_utc TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        checksum_sha256 TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS upload_attempt (
        attempt_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        fencing_token INTEGER NOT NULL CHECK (fencing_token >= 0),
        hub_status TEXT NOT NULL,
        last_heartbeat_at_utc TEXT,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        checksum_sha256 TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS process_identity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        execution_id TEXT NOT NULL REFERENCES upload_execution(execution_id),
        attempt_id TEXT NOT NULL,
        pid INTEGER NOT NULL CHECK (pid > 0),
        process_create_time INTEGER NOT NULL CHECK (process_create_time >= 0),
        job_object_name TEXT NOT NULL,
        role TEXT NOT NULL,
        recorded_at_utc TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        checksum_sha256 TEXT NOT NULL,
        UNIQUE(execution_id, pid, process_create_time)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS termination_evidence (
        event_id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL,
        execution_id TEXT NOT NULL REFERENCES upload_execution(execution_id),
        job_id TEXT NOT NULL,
        worker_id TEXT NOT NULL,
        fencing_token INTEGER NOT NULL CHECK (fencing_token >= 0),
        pid INTEGER NOT NULL,
        process_create_time INTEGER NOT NULL,
        job_object_name TEXT NOT NULL,
        verification_result TEXT NOT NULL,
        termination_reason TEXT NOT NULL,
        evidence_source TEXT NOT NULL,
        watchdog_version TEXT NOT NULL,
        termination_requested_at_utc TEXT NOT NULL,
        verified_at_utc TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        checksum_sha256 TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS watchdog_checkpoint (
        worker_id TEXT PRIMARY KEY,
        last_boot_time_utc TEXT NOT NULL,
        last_scan_at_utc TEXT NOT NULL,
        watchdog_pid INTEGER NOT NULL,
        watchdog_version TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        checksum_sha256 TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_exec_status ON upload_execution(status);",
    "CREATE INDEX IF NOT EXISTS idx_exec_attempt ON upload_execution(attempt_id);",
    "CREATE INDEX IF NOT EXISTS idx_identity_exec ON process_identity(execution_id);",
    "CREATE INDEX IF NOT EXISTS idx_evidence_exec ON termination_evidence(execution_id);",
]


# ---------------------------------------------------------------------------
# StateStore
# ---------------------------------------------------------------------------


class StateStore:
    """Durable local SQLite State Store for Watchdog execution & attempt state."""

    def __init__(self, db_path: Union[str, Path]) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._closed = False
        self._unsafe_state = False
        self._initialize()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _initialize(self) -> None:
        """Open SQLite connection, enforce PRAGMAs, and validate/create schema."""
        conn: Optional[sqlite3.Connection] = None
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path),
                timeout=5.0,
                isolation_level=None,  # Explicit transaction control
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            self._apply_pragmas(conn)
            self._ensure_schema(conn)
            self._conn = conn
        except Exception as exc:
            self._conn = None
            self._closed = True
            self._unsafe_state = True
            close_exc: Optional[BaseException] = None
            if conn is not None:
                try:
                    conn.close()
                except Exception as c_exc:
                    close_exc = c_exc
                    logger.critical(
                        "Failed to close SQLite connection after initialization error",
                        extra={"original_error": str(exc), "close_error": str(c_exc)},
                    )
            if isinstance(exc, StateStoreError):
                if close_exc is not None:
                    raise StateStorePersistenceError(
                        f"{exc}; additionally failed to close connection: {close_exc}"
                    ) from exc
                raise
            if close_exc is not None:
                raise StateStorePersistenceError(
                    f"Failed to initialize StateStore at '{self._db_path}': {exc}; "
                    f"additionally failed to close connection: {close_exc}"
                ) from exc
            raise StateStorePersistenceError(
                f"Failed to initialize StateStore at '{self._db_path}': {exc}"
            ) from exc

    @staticmethod
    def _apply_pragmas(conn: sqlite3.Connection) -> None:
        """Apply mandatory SQLite durability and concurrency PRAGMAs."""
        journal_mode = conn.execute("PRAGMA journal_mode = WAL;").fetchone()[0]
        if str(journal_mode).lower() != "wal":
            raise StateStorePersistenceError(
                f"Expected SQLite journal_mode=WAL, got '{journal_mode}'"
            )
        conn.execute("PRAGMA synchronous = FULL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        """Create schema if fresh, or validate schema_version if existing."""
        conn.execute("BEGIN IMMEDIATE;")
        try:
            existing_meta = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_metadata';"
            ).fetchone()
            if existing_meta is None:
                # Check if another table exists without schema_metadata (incompatible/legacy)
                other_tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
                ).fetchall()
                if other_tables:
                    raise StateStoreSchemaError(
                        "Incompatible database: missing schema_metadata table"
                    )
                for stmt in _SCHEMA_STATEMENTS:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_metadata (key, value) VALUES ('schema_version', ?);",
                    (str(SCHEMA_VERSION),),
                )
            else:
                row = conn.execute(
                    "SELECT value FROM schema_metadata WHERE key = 'schema_version';"
                ).fetchone()
                if row is None:
                    raise StateStoreSchemaError("Missing schema_version in schema_metadata")
                try:
                    found_version = int(row["value"])
                except (TypeError, ValueError) as exc:
                    raise StateStoreSchemaError(
                        f"Invalid schema_version value: {row['value']}"
                    ) from exc
                if found_version != SCHEMA_VERSION:
                    raise StateStoreSchemaError(
                        f"Incompatible schema_version: expected {SCHEMA_VERSION}, found {found_version}"
                    )
            if conn.in_transaction:
                conn.execute("COMMIT;")
        except Exception as orig_exc:
            if conn.in_transaction:
                try:
                    conn.execute("ROLLBACK;")
                except sqlite3.Error as rb_exc:
                    logger.critical(
                        "Schema initialization rollback failed",
                        extra={
                            "original_error": str(orig_exc),
                            "rollback_error": str(rb_exc),
                        },
                    )
                    raise StateStorePersistenceError(
                        f"Schema initialization failed ({orig_exc}) and rollback failed ({rb_exc})"
                    ) from orig_exc
            raise

    def _require_conn(self) -> sqlite3.Connection:
        if self._unsafe_state:
            raise StateStorePersistenceError(
                "StateStore is in an unsafe state after a failed rollback; reopen required"
            )
        if self._closed or self._conn is None:
            raise StateStorePersistenceError("StateStore connection is closed")
        return self._conn

    def _enter_unsafe_state(self, original_exc: BaseException, rollback_exc: BaseException) -> None:
        """Invalidate connection when rollback fails so it is never silently reused."""
        logger.critical(
            "StateStore rollback failed; entering unsafe state",
            extra={
                "original_error": str(original_exc),
                "rollback_error": str(rollback_exc),
            },
        )
        self._unsafe_state = True
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
            self._closed = True

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Explicit write transaction with safe rollback and unsafe-state quarantine."""
        with self._lock:
            conn = self._require_conn()
            try:
                conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.Error as exc:
                raise StateStorePersistenceError(f"Failed to begin transaction: {exc}") from exc
            try:
                yield conn
                conn.execute("COMMIT;")
            except StateStoreError as orig_exc:
                try:
                    conn.execute("ROLLBACK;")
                except sqlite3.Error as rb_exc:
                    self._enter_unsafe_state(orig_exc, rb_exc)
                raise
            except Exception as orig_exc:
                try:
                    conn.execute("ROLLBACK;")
                except sqlite3.Error as rb_exc:
                    self._enter_unsafe_state(orig_exc, rb_exc)
                    raise StateStorePersistenceError(
                        f"Transaction failed ({orig_exc}) and rollback failed ({rb_exc}); connection invalidated"
                    ) from orig_exc
                raise StateStorePersistenceError(f"Transaction failed and rolled back: {orig_exc}") from orig_exc

    def close(self) -> None:
        """Safely close the SQLite connection (idempotent)."""
        with self._lock:
            if not self._closed and self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
                    self._closed = True

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def get_pragmas(self) -> Dict[str, Any]:
        """Return active SQLite PRAGMA settings for verification."""
        with self._lock:
            conn = self._require_conn()
            jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            sync = conn.execute("PRAGMA synchronous;").fetchone()[0]
            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            bt = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            return {
                "journal_mode": str(jm).lower(),
                "synchronous": int(sync),
                "foreign_keys": int(fk),
                "busy_timeout": int(bt),
            }

    # -----------------------------------------------------------------------
    # Write Operations
    # -----------------------------------------------------------------------

    def record_execution(
        self,
        identity: CanonicalExecutionIdentity,
        boot_time_utc: str,
        *,
        status: ExecutionStatus = ExecutionStatus.RUNNING,
        role: str = "UPLOADER_MAIN",
    ) -> ExecutionRecord:
        """Persist a new execution and its initial canonical process identity atomically."""
        now = utc_now_iso()
        exec_rec = ExecutionRecord(
            execution_id=identity.execution_id,
            attempt_id=identity.attempt_id,
            job_id=identity.job_id,
            tenant_id=identity.tenant_id,
            channel_id=identity.channel_id,
            worker_id=identity.worker_id,
            fencing_token=identity.fencing_token,
            browser_user_data_dir=identity.browser_user_data_dir,
            boot_time_utc=boot_time_utc,
            status=status,
            created_at_utc=now,
            updated_at_utc=now,
            schema_version=SCHEMA_VERSION,
        )
        exec_checksum = compute_canonical_checksum(_execution_checksum_payload(exec_rec))
        exec_rec = ExecutionRecord(**{**exec_rec.__dict__, "checksum_sha256": exec_checksum})

        pid_rec = ProcessIdentityRecord(
            execution_id=identity.execution_id,
            attempt_id=identity.attempt_id,
            pid=identity.pid,
            process_create_time=identity.process_create_time,
            job_object_name=identity.job_object_name,
            role=role,
            recorded_at_utc=now,
            schema_version=SCHEMA_VERSION,
        )
        pid_checksum = compute_canonical_checksum(_process_identity_checksum_payload(pid_rec))
        pid_rec = ProcessIdentityRecord(**{**pid_rec.__dict__, "checksum_sha256": pid_checksum})

        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO upload_execution (
                    execution_id, attempt_id, job_id, tenant_id, channel_id,
                    worker_id, fencing_token, browser_user_data_dir, boot_time_utc,
                    status, created_at_utc, updated_at_utc, schema_version, checksum_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    exec_rec.execution_id,
                    exec_rec.attempt_id,
                    exec_rec.job_id,
                    exec_rec.tenant_id,
                    exec_rec.channel_id,
                    exec_rec.worker_id,
                    exec_rec.fencing_token,
                    exec_rec.browser_user_data_dir,
                    exec_rec.boot_time_utc,
                    exec_rec.status.value,
                    exec_rec.created_at_utc,
                    exec_rec.updated_at_utc,
                    exec_rec.schema_version,
                    exec_rec.checksum_sha256,
                ),
            )
            conn.execute(
                """
                INSERT INTO process_identity (
                    execution_id, attempt_id, pid, process_create_time,
                    job_object_name, role, recorded_at_utc, schema_version, checksum_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    pid_rec.execution_id,
                    pid_rec.attempt_id,
                    pid_rec.pid,
                    pid_rec.process_create_time,
                    pid_rec.job_object_name,
                    pid_rec.role,
                    pid_rec.recorded_at_utc,
                    pid_rec.schema_version,
                    pid_rec.checksum_sha256,
                ),
            )
        return exec_rec

    def upsert_attempt(
        self,
        attempt_id: str,
        job_id: str,
        fencing_token: int,
        hub_status: AttemptHubStatus = AttemptHubStatus.INTENT,
        last_heartbeat_at_utc: Optional[str] = None,
    ) -> AttemptRecord:
        """Create or update an upload attempt, rejecting stale fencing_token."""
        if fencing_token < 0:
            raise StateStoreConflictError("fencing_token must be non-negative")

        now = utc_now_iso()
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM upload_attempt WHERE attempt_id = ?;",
                (attempt_id,),
            ).fetchone()

            if existing is not None:
                current = self._validate_and_build_attempt(existing)
                if current.job_id != job_id:
                    raise StateStoreConflictError(
                        f"job_id mismatch for attempt {attempt_id}: expected '{current.job_id}', got '{job_id}'"
                    )
                if fencing_token < current.fencing_token:
                    raise StateStoreConflictError(
                        f"Stale fencing_token {fencing_token} < existing {current.fencing_token} for attempt {attempt_id}"
                    )
                # Both equal (==) and higher (>) fencing_token are permitted for forward/idempotent updates
                created_at = current.created_at_utc
            else:
                created_at = now

            rec = AttemptRecord(
                attempt_id=attempt_id,
                job_id=job_id,
                fencing_token=fencing_token,
                hub_status=hub_status,
                last_heartbeat_at_utc=last_heartbeat_at_utc,
                created_at_utc=created_at,
                updated_at_utc=now,
                schema_version=SCHEMA_VERSION,
            )
            checksum = compute_canonical_checksum(_attempt_checksum_payload(rec))
            rec = AttemptRecord(**{**rec.__dict__, "checksum_sha256": checksum})

            conn.execute(
                """
                INSERT INTO upload_attempt (
                    attempt_id, job_id, fencing_token, hub_status,
                    last_heartbeat_at_utc, created_at_utc, updated_at_utc,
                    schema_version, checksum_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id) DO UPDATE SET
                    job_id = excluded.job_id,
                    fencing_token = excluded.fencing_token,
                    hub_status = excluded.hub_status,
                    last_heartbeat_at_utc = excluded.last_heartbeat_at_utc,
                    updated_at_utc = excluded.updated_at_utc,
                    schema_version = excluded.schema_version,
                    checksum_sha256 = excluded.checksum_sha256;
                """,
                (
                    rec.attempt_id,
                    rec.job_id,
                    rec.fencing_token,
                    rec.hub_status.value,
                    rec.last_heartbeat_at_utc,
                    rec.created_at_utc,
                    rec.updated_at_utc,
                    rec.schema_version,
                    rec.checksum_sha256,
                ),
            )
        return rec

    def update_execution_status(
        self,
        execution_id: str,
        new_status: ExecutionStatus,
        *,
        expected_fencing_token: int,
    ) -> ExecutionRecord:
        """Update execution status only if fencing_token matches and current record is valid."""
        now = utc_now_iso()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM upload_execution WHERE execution_id = ?;",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise StateStorePersistenceError(f"Execution '{execution_id}' not found")

            current = self._validate_and_build_execution(row)
            if expected_fencing_token < current.fencing_token:
                raise StateStoreConflictError(
                    f"Stale fencing_token {expected_fencing_token} < {current.fencing_token} for execution {execution_id}"
                )
            if expected_fencing_token != current.fencing_token:
                raise StateStoreConflictError(
                    f"Fencing token mismatch for execution {execution_id}: expected {current.fencing_token}, got {expected_fencing_token}"
                )

            updated = ExecutionRecord(
                execution_id=current.execution_id,
                attempt_id=current.attempt_id,
                job_id=current.job_id,
                tenant_id=current.tenant_id,
                channel_id=current.channel_id,
                worker_id=current.worker_id,
                fencing_token=current.fencing_token,
                browser_user_data_dir=current.browser_user_data_dir,
                boot_time_utc=current.boot_time_utc,
                status=new_status,
                created_at_utc=current.created_at_utc,
                updated_at_utc=now,
                schema_version=SCHEMA_VERSION,
            )
            checksum = compute_canonical_checksum(_execution_checksum_payload(updated))
            updated = ExecutionRecord(**{**updated.__dict__, "checksum_sha256": checksum})

            conn.execute(
                """
                UPDATE upload_execution
                SET status = ?, updated_at_utc = ?, schema_version = ?, checksum_sha256 = ?
                WHERE execution_id = ?;
                """,
                (
                    updated.status.value,
                    updated.updated_at_utc,
                    updated.schema_version,
                    updated.checksum_sha256,
                    updated.execution_id,
                ),
            )
        return updated

    def update_heartbeat(
        self,
        attempt_id: str,
        *,
        expected_fencing_token: int,
        heartbeat_at_utc: Optional[str] = None,
    ) -> AttemptRecord:
        """Update last_heartbeat_at_utc for an attempt after validating fencing_token."""
        ts = heartbeat_at_utc or utc_now_iso()
        now = utc_now_iso()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM upload_attempt WHERE attempt_id = ?;",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise StateStorePersistenceError(f"Attempt '{attempt_id}' not found")

            current = self._validate_and_build_attempt(row)
            if expected_fencing_token < current.fencing_token:
                raise StateStoreConflictError(
                    f"Stale fencing_token {expected_fencing_token} < {current.fencing_token}"
                )
            if expected_fencing_token != current.fencing_token:
                raise StateStoreConflictError(
                    f"Fencing token mismatch: expected {current.fencing_token}, got {expected_fencing_token}"
                )

            updated = AttemptRecord(
                attempt_id=current.attempt_id,
                job_id=current.job_id,
                fencing_token=current.fencing_token,
                hub_status=current.hub_status,
                last_heartbeat_at_utc=ts,
                created_at_utc=current.created_at_utc,
                updated_at_utc=now,
                schema_version=SCHEMA_VERSION,
            )
            checksum = compute_canonical_checksum(_attempt_checksum_payload(updated))
            updated = AttemptRecord(**{**updated.__dict__, "checksum_sha256": checksum})

            conn.execute(
                """
                UPDATE upload_attempt
                SET last_heartbeat_at_utc = ?, updated_at_utc = ?, schema_version = ?, checksum_sha256 = ?
                WHERE attempt_id = ?;
                """,
                (
                    updated.last_heartbeat_at_utc,
                    updated.updated_at_utc,
                    updated.schema_version,
                    updated.checksum_sha256,
                    updated.attempt_id,
                ),
            )
        return updated

    def save_evidence(self, evidence: TerminationEvidenceRecord) -> TerminationEvidenceRecord:
        """Persist termination evidence after validating identity, payload_json, and checksum."""
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
        rec = TerminationEvidenceRecord(**{**rec.__dict__, "checksum_sha256": checksum})

        with self._transaction() as conn:
            exec_row = conn.execute(
                "SELECT * FROM upload_execution WHERE execution_id = ?;",
                (rec.execution_id,),
            ).fetchone()
            if exec_row is None:
                raise StateStorePersistenceError(
                    f"Referenced execution '{rec.execution_id}' does not exist"
                )
            exec_rec = self._validate_and_build_execution(exec_row)

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
            valid_identities = [self._validate_and_build_identity(r) for r in id_rows]
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
        return rec

    def save_checkpoint(self, checkpoint: CheckpointRecord) -> CheckpointRecord:
        """Persist or update watchdog checkpoint."""
        rec = CheckpointRecord(**{**checkpoint.__dict__, "schema_version": SCHEMA_VERSION})
        checksum = compute_canonical_checksum(_checkpoint_checksum_payload(rec))
        rec = CheckpointRecord(**{**rec.__dict__, "checksum_sha256": checksum})

        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO watchdog_checkpoint (
                    worker_id, last_boot_time_utc, last_scan_at_utc,
                    watchdog_pid, watchdog_version, schema_version, checksum_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    last_boot_time_utc = excluded.last_boot_time_utc,
                    last_scan_at_utc = excluded.last_scan_at_utc,
                    watchdog_pid = excluded.watchdog_pid,
                    watchdog_version = excluded.watchdog_version,
                    schema_version = excluded.schema_version,
                    checksum_sha256 = excluded.checksum_sha256;
                """,
                (
                    rec.worker_id,
                    rec.last_boot_time_utc,
                    rec.last_scan_at_utc,
                    rec.watchdog_pid,
                    rec.watchdog_version,
                    rec.schema_version,
                    rec.checksum_sha256,
                ),
            )
        return rec

    # -----------------------------------------------------------------------
    # Read & Integrity Verification Operations
    # -----------------------------------------------------------------------

    def _validate_and_build_execution(self, row: sqlite3.Row) -> ExecutionRecord:
        if int(row["schema_version"]) != SCHEMA_VERSION:
            raise StateStoreCorruptionError(
                f"Incompatible execution schema_version={row['schema_version']} for {row['execution_id']}"
            )
        try:
            status = ExecutionStatus(row["status"])
        except ValueError as exc:
            raise StateStoreCorruptionError(
                f"Invalid execution status '{row['status']}' for {row['execution_id']}"
            ) from exc

        rec = ExecutionRecord(
            execution_id=row["execution_id"],
            attempt_id=row["attempt_id"],
            job_id=row["job_id"],
            tenant_id=row["tenant_id"],
            channel_id=row["channel_id"],
            worker_id=row["worker_id"],
            fencing_token=int(row["fencing_token"]),
            browser_user_data_dir=row["browser_user_data_dir"],
            boot_time_utc=row["boot_time_utc"],
            status=status,
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
            schema_version=int(row["schema_version"]),
            checksum_sha256=row["checksum_sha256"],
        )
        expected = compute_canonical_checksum(_execution_checksum_payload(rec))
        if rec.checksum_sha256 != expected:
            logger.error(
                "StateStore corruption detected in upload_execution",
                extra={"execution_id": rec.execution_id, "classification": "INCONCLUSIVE"},
            )
            raise StateStoreCorruptionError(
                f"Checksum mismatch for execution {rec.execution_id} (classified INCONCLUSIVE)"
            )
        return rec

    def _validate_and_build_attempt(self, row: sqlite3.Row) -> AttemptRecord:
        if int(row["schema_version"]) != SCHEMA_VERSION:
            raise StateStoreCorruptionError(
                f"Incompatible attempt schema_version={row['schema_version']} for {row['attempt_id']}"
            )
        try:
            hub_status = AttemptHubStatus(row["hub_status"])
        except ValueError as exc:
            raise StateStoreCorruptionError(
                f"Invalid hub_status '{row['hub_status']}' for {row['attempt_id']}"
            ) from exc

        rec = AttemptRecord(
            attempt_id=row["attempt_id"],
            job_id=row["job_id"],
            fencing_token=int(row["fencing_token"]),
            hub_status=hub_status,
            last_heartbeat_at_utc=row["last_heartbeat_at_utc"],
            created_at_utc=row["created_at_utc"],
            updated_at_utc=row["updated_at_utc"],
            schema_version=int(row["schema_version"]),
            checksum_sha256=row["checksum_sha256"],
        )
        expected = compute_canonical_checksum(_attempt_checksum_payload(rec))
        if rec.checksum_sha256 != expected:
            logger.error(
                "StateStore corruption detected in upload_attempt",
                extra={"attempt_id": rec.attempt_id, "classification": "INCONCLUSIVE"},
            )
            raise StateStoreCorruptionError(
                f"Checksum mismatch for attempt {rec.attempt_id} (classified INCONCLUSIVE)"
            )
        return rec

    def _validate_and_build_identity(self, row: sqlite3.Row) -> ProcessIdentityRecord:
        if int(row["schema_version"]) != SCHEMA_VERSION:
            raise StateStoreCorruptionError(
                f"Incompatible process_identity schema_version={row['schema_version']}"
            )
        rec = ProcessIdentityRecord(
            record_id=int(row["id"]),
            execution_id=row["execution_id"],
            attempt_id=row["attempt_id"],
            pid=int(row["pid"]),
            process_create_time=int(row["process_create_time"]),
            job_object_name=row["job_object_name"],
            role=row["role"],
            recorded_at_utc=row["recorded_at_utc"],
            schema_version=int(row["schema_version"]),
            checksum_sha256=row["checksum_sha256"],
        )
        expected = compute_canonical_checksum(_process_identity_checksum_payload(rec))
        if rec.checksum_sha256 != expected:
            logger.error(
                "StateStore corruption detected in process_identity",
                extra={"execution_id": rec.execution_id, "classification": "INCONCLUSIVE"},
            )
            raise StateStoreCorruptionError(
                f"Checksum mismatch for process_identity id={rec.record_id} (classified INCONCLUSIVE)"
            )
        return rec

    def _validate_and_build_evidence(self, row: sqlite3.Row) -> TerminationEvidenceRecord:
        if int(row["schema_version"]) != SCHEMA_VERSION:
            raise StateStoreCorruptionError(
                f"Incompatible termination_evidence schema_version={row['schema_version']}"
            )
        rec = TerminationEvidenceRecord(
            event_id=row["event_id"],
            attempt_id=row["attempt_id"],
            execution_id=row["execution_id"],
            job_id=row["job_id"],
            worker_id=row["worker_id"],
            fencing_token=int(row["fencing_token"]),
            pid=int(row["pid"]),
            process_create_time=int(row["process_create_time"]),
            job_object_name=row["job_object_name"],
            verification_result=row["verification_result"],
            termination_reason=row["termination_reason"],
            evidence_source=row["evidence_source"],
            watchdog_version=row["watchdog_version"],
            termination_requested_at_utc=row["termination_requested_at_utc"],
            verified_at_utc=row["verified_at_utc"],
            payload_json=row["payload_json"],
            schema_version=int(row["schema_version"]),
            checksum_sha256=row["checksum_sha256"],
        )
        expected = compute_canonical_checksum(_evidence_checksum_payload(rec))
        if rec.checksum_sha256 != expected:
            logger.error(
                "StateStore corruption detected in termination_evidence",
                extra={"event_id": rec.event_id, "classification": "INCONCLUSIVE"},
            )
            raise StateStoreCorruptionError(
                f"Checksum mismatch for termination_evidence {rec.event_id} (classified INCONCLUSIVE)"
            )
        return rec

    def _validate_and_build_checkpoint(self, row: sqlite3.Row) -> CheckpointRecord:
        if int(row["schema_version"]) != SCHEMA_VERSION:
            raise StateStoreCorruptionError(
                f"Incompatible watchdog_checkpoint schema_version={row['schema_version']}"
            )
        rec = CheckpointRecord(
            worker_id=row["worker_id"],
            last_boot_time_utc=row["last_boot_time_utc"],
            last_scan_at_utc=row["last_scan_at_utc"],
            watchdog_pid=int(row["watchdog_pid"]),
            watchdog_version=row["watchdog_version"],
            schema_version=int(row["schema_version"]),
            checksum_sha256=row["checksum_sha256"],
        )
        expected = compute_canonical_checksum(_checkpoint_checksum_payload(rec))
        if rec.checksum_sha256 != expected:
            logger.error(
                "StateStore corruption detected in watchdog_checkpoint",
                extra={"worker_id": rec.worker_id, "classification": "INCONCLUSIVE"},
            )
            raise StateStoreCorruptionError(
                f"Checksum mismatch for watchdog_checkpoint {rec.worker_id} (classified INCONCLUSIVE)"
            )
        return rec

    def get_checkpoint(self, worker_id: str) -> Optional[CheckpointRecord]:
        """Retrieve and validate watchdog checkpoint by worker_id."""
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                "SELECT * FROM watchdog_checkpoint WHERE worker_id = ?;",
                (worker_id,),
            ).fetchone()
            if row is None:
                return None
            return self._validate_and_build_checkpoint(row)

    def get_execution(self, execution_id: str) -> Optional[ExecutionRecord]:
        """Retrieve and validate an execution by execution_id."""
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                "SELECT * FROM upload_execution WHERE execution_id = ?;",
                (execution_id,),
            ).fetchone()
            if row is None:
                return None
            return self._validate_and_build_execution(row)

    def get_attempt(self, attempt_id: str) -> Optional[AttemptRecord]:
        """Retrieve and validate an attempt by attempt_id."""
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                "SELECT * FROM upload_attempt WHERE attempt_id = ?;",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            return self._validate_and_build_attempt(row)

    def get_process_identities(self, execution_id: str) -> List[ProcessIdentityRecord]:
        """Retrieve and validate all process identities for an execution."""
        with self._lock:
            conn = self._require_conn()
            rows = conn.execute(
                "SELECT * FROM process_identity WHERE execution_id = ? ORDER BY id ASC;",
                (execution_id,),
            ).fetchall()
            return [self._validate_and_build_identity(r) for r in rows]

    def get_evidence(self, event_id: str) -> Optional[TerminationEvidenceRecord]:
        """Retrieve and validate termination evidence by event_id."""
        with self._lock:
            conn = self._require_conn()
            row = conn.execute(
                "SELECT * FROM termination_evidence WHERE event_id = ?;",
                (event_id,),
            ).fetchone()
            if row is None:
                return None
            return self._validate_and_build_evidence(row)

    def list_executions_by_status(
        self, statuses: Optional[List[ExecutionStatus]] = None
    ) -> List[ExecutionRecord]:
        """List executions, optionally filtered by status, validating each record."""
        with self._lock:
            conn = self._require_conn()
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = conn.execute(
                    f"SELECT * FROM upload_execution WHERE status IN ({placeholders}) ORDER BY created_at_utc ASC;",
                    tuple(s.value for s in statuses),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM upload_execution ORDER BY created_at_utc ASC;"
                ).fetchall()
            return [self._validate_and_build_execution(r) for r in rows]

    def verify_integrity(self) -> List[IntegrityVerificationReport]:
        """Scan all 5 persisted tables and return an integrity report per record.

        Corrupted or structurally invalid records are classified as INCONCLUSIVE
        with raw diagnostics preserved, never promoted to valid evidence and
        never interrupting verification of remaining records.
        """
        reports: List[IntegrityVerificationReport] = []
        checks = [
            ("upload_execution", "execution_id", self._validate_and_build_execution),
            ("upload_attempt", "attempt_id", self._validate_and_build_attempt),
            ("process_identity", "id", self._validate_and_build_identity),
            ("termination_evidence", "event_id", self._validate_and_build_evidence),
            ("watchdog_checkpoint", "worker_id", self._validate_and_build_checkpoint),
        ]
        with self._lock:
            conn = self._require_conn()
            for table, key_col, validator in checks:
                for row in conn.execute(f"SELECT * FROM {table};").fetchall():
                    key = str(row[key_col])
                    try:
                        validator(row)
                        reports.append(IntegrityVerificationReport(IntegrityStatus.VALID, table, key))
                    except (StateStoreCorruptionError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                        reports.append(
                            IntegrityVerificationReport(
                                IntegrityStatus.INCONCLUSIVE,
                                table,
                                key,
                                reason=str(exc),
                                raw_diagnostic=dict(row),
                            )
                        )
        return reports
