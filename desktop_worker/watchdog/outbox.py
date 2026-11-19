"""Durable local SQLite Outbox for the Local Watchdog (Sprint 2.7 - Task 4).

Provides transactional, checksum-verified event enqueueing, atomic lease-based
claiming, exponential backoff with jitter, dead-letter handling, expired lease
recovery, and strict ACK validation on top of the Task 3 StateStore SQLite DB.

Delivery guarantee: at-least-once with idempotent processing keyed by stable
`event_id`. This module does NOT implement the full Watchdog daemon, YouTube
reconciliation, upload retries, or real Cloud Hub HTTP endpoints.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from desktop_worker.watchdog.state_store import (
    IntegrityStatus,
    IntegrityVerificationReport,
    StateStore,
    StateStoreConflictError,
    StateStoreCorruptionError,
    StateStoreError,
    StateStorePersistenceError,
    StateStoreSchemaError,
    compute_canonical_checksum,
)

logger = logging.getLogger(__name__)

OUTBOX_SCHEMA_VERSION = 2
_OUTBOX_SCHEMA_META_KEY = "outbox_schema_version"


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def format_utc_iso(dt: datetime) -> str:
    """Format a datetime as UTC ISO-8601 string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_utc_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime."""
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Enums & Exceptions
# ---------------------------------------------------------------------------


class OutboxEventStatus(str, Enum):
    """Explicit lifecycle states for a durable outbox event."""

    PENDING = "PENDING"
    IN_FLIGHT = "IN_FLIGHT"
    DELIVERED = "DELIVERED"
    RETRY = "RETRY"
    DEAD_LETTER = "DEAD_LETTER"


_VALID_TRANSITIONS: Dict[OutboxEventStatus, Tuple[OutboxEventStatus, ...]] = {
    OutboxEventStatus.PENDING: (
        OutboxEventStatus.IN_FLIGHT,
        OutboxEventStatus.DEAD_LETTER,
    ),
    OutboxEventStatus.IN_FLIGHT: (
        OutboxEventStatus.DELIVERED,
        OutboxEventStatus.RETRY,
        OutboxEventStatus.DEAD_LETTER,
        OutboxEventStatus.PENDING,  # Lease expiration recovery
    ),
    OutboxEventStatus.RETRY: (
        OutboxEventStatus.IN_FLIGHT,
        OutboxEventStatus.DEAD_LETTER,
    ),
    OutboxEventStatus.DELIVERED: (),
    OutboxEventStatus.DEAD_LETTER: (),
}


class OutboxError(StateStoreError):
    """Base exception for all Outbox failures."""


class OutboxSchemaError(StateStoreSchemaError):
    """Raised when the outbox_event schema version is incompatible."""


class OutboxCorruptionError(StateStoreCorruptionError):
    """Raised when an outbox_event record fails checksum or structural validation."""


class OutboxConflictError(StateStoreConflictError):
    """Raised on duplicate conflicting event_id, stale fencing_token, or lease mismatch."""


class OutboxStateTransitionError(OutboxError):
    """Raised when an invalid status transition is attempted."""


class OutboxAckError(OutboxError):
    """Raised when a Cloud Hub ACK is invalid, ambiguous, or mismatched."""


class OutboxPayloadError(OutboxError):
    """Raised when an outbox event payload is invalid."""


# ---------------------------------------------------------------------------
# Policies & Contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryPolicy:
    """Configurable exponential backoff with jitter and maximum attempt cap."""

    max_attempts: int = 5
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 300.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be > 0")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be >= base_delay_seconds")
        if not (0.0 <= self.jitter_ratio <= 1.0):
            raise ValueError("jitter_ratio must be between 0.0 and 1.0")

    def compute_delay_seconds(
        self,
        attempt_count: int,
        rng: Optional[Callable[[float, float], float]] = None,
    ) -> float:
        """Compute bounded exponential backoff delay with symmetric jitter."""
        exponent = max(0, attempt_count - 1)
        raw_delay = min(self.base_delay_seconds * (2 ** exponent), self.max_delay_seconds)
        if self.jitter_ratio == 0.0:
            return raw_delay
        jitter_span = raw_delay * self.jitter_ratio
        rand_fn = rng or random.uniform
        jitter = rand_fn(-jitter_span, jitter_span)
        return max(0.0, min(self.max_delay_seconds, raw_delay + jitter))


@dataclass(frozen=True)
class HubAckResponse:
    """Minimal Cloud Hub ACK contract required to confirm outbox delivery.

    Note: The current Cloud Hub does not yet expose a Watchdog event ingestion
    endpoint with idempotency and structured ACK. This dataclass defines the
    minimal contract required for safe confirmation.
    """

    accepted: bool
    event_id: str
    ack_id: Optional[str] = None
    http_status: Optional[int] = None
    permanent_rejection: bool = False
    error_message: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


class OutboxTransport(Protocol):
    """Minimal transport interface for delivering a claimed outbox event."""

    def send_event(
        self,
        event: "OutboxEventRecord",
        *,
        idempotency_key: str,
    ) -> HubAckResponse:
        """Send an event to Cloud Hub and return a structured HubAckResponse."""
        ...


class OutboxTemporaryDeliveryError(Exception):
    """Raised by a transport on transient network/timeout/5xx/429 failures."""


class OutboxPermanentDeliveryError(Exception):
    """Raised by a transport on permanent 4xx/validation rejections."""


# ---------------------------------------------------------------------------
# Data Model & Checksum
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutboxEventRecord:
    """Persisted durable outbox event record.

    Counter semantics:
    - `claim_count`: total number of durable IN_FLIGHT leases started for this event.
    - `attempt_count`: number of delivery attempts counted toward `RetryPolicy.max_attempts`,
      including both explicit delivery failures (`mark_retry` / `mark_dead_letter`) and
      abandoned leases recovered after expiration (`recover_expired_leases`).
    """

    event_id: str
    event_type: str
    execution_id: Optional[str]
    attempt_id: Optional[str]
    job_id: Optional[str]
    worker_id: str
    fencing_token: Optional[int]
    payload_json: str
    created_at_utc: str
    available_at_utc: str
    attempt_count: int
    last_attempt_at_utc: Optional[str]
    delivered_at_utc: Optional[str]
    last_error: Optional[str]
    status: OutboxEventStatus
    claim_count: int = 0
    lease_id: Optional[str] = None
    lease_owner: Optional[str] = None
    lease_expires_at_utc: Optional[str] = None
    ack_id: Optional[str] = None
    schema_version: int = OUTBOX_SCHEMA_VERSION
    checksum_sha256: str = ""


def _outbox_checksum_payload_v1(row: sqlite3.Row) -> Dict[str, Any]:
    """Legacy v1 canonical checksum payload used only during v1 -> v2 migration."""
    fencing_raw = row["fencing_token"]
    return {
        "schema_version": int(row["schema_version"]),
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "execution_id": row["execution_id"],
        "attempt_id": row["attempt_id"],
        "job_id": row["job_id"],
        "worker_id": row["worker_id"],
        "fencing_token": int(fencing_raw) if fencing_raw is not None else None,
        "payload_json": row["payload_json"],
        "created_at_utc": row["created_at_utc"],
        "available_at_utc": row["available_at_utc"],
        "attempt_count": int(row["attempt_count"]),
        "last_attempt_at_utc": row["last_attempt_at_utc"],
        "delivered_at_utc": row["delivered_at_utc"],
        "last_error": row["last_error"],
        "status": str(row["status"]),
        "lease_owner": row["lease_owner"],
        "lease_expires_at_utc": row["lease_expires_at_utc"],
        "ack_id": row["ack_id"],
    }


def _outbox_checksum_payload(rec: OutboxEventRecord) -> Dict[str, Any]:
    return {
        "schema_version": rec.schema_version,
        "event_id": rec.event_id,
        "event_type": rec.event_type,
        "execution_id": rec.execution_id,
        "attempt_id": rec.attempt_id,
        "job_id": rec.job_id,
        "worker_id": rec.worker_id,
        "fencing_token": rec.fencing_token,
        "payload_json": rec.payload_json,
        "created_at_utc": rec.created_at_utc,
        "available_at_utc": rec.available_at_utc,
        "claim_count": rec.claim_count,
        "attempt_count": rec.attempt_count,
        "last_attempt_at_utc": rec.last_attempt_at_utc,
        "delivered_at_utc": rec.delivered_at_utc,
        "last_error": rec.last_error,
        "status": rec.status.value if isinstance(rec.status, OutboxEventStatus) else str(rec.status),
        "lease_id": rec.lease_id,
        "lease_owner": rec.lease_owner,
        "lease_expires_at_utc": rec.lease_expires_at_utc,
        "ack_id": rec.ack_id,
    }


def _canonicalize_payload(payload: Any) -> str:
    """Validate and canonicalize an event payload into a JSON object string."""
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise OutboxPayloadError(f"Invalid payload_json: {exc}") from exc
    elif isinstance(payload, dict):
        parsed = payload
    else:
        raise OutboxPayloadError("Event payload must be a dict or JSON object string")

    if not isinstance(parsed, dict):
        raise OutboxPayloadError("Event payload must serialize to a JSON object")

    try:
        return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise OutboxPayloadError(f"Event payload is not JSON-serializable: {exc}") from exc


# ---------------------------------------------------------------------------
# SQLite Schema & Migration
# ---------------------------------------------------------------------------

_OUTBOX_V1_REQUIRED_COLUMNS = frozenset(
    {
        "event_id",
        "event_type",
        "execution_id",
        "attempt_id",
        "job_id",
        "worker_id",
        "fencing_token",
        "payload_json",
        "created_at_utc",
        "available_at_utc",
        "attempt_count",
        "last_attempt_at_utc",
        "delivered_at_utc",
        "last_error",
        "status",
        "lease_owner",
        "lease_expires_at_utc",
        "ack_id",
        "schema_version",
        "checksum_sha256",
    }
)

_OUTBOX_V2_REQUIRED_COLUMNS = _OUTBOX_V1_REQUIRED_COLUMNS | frozenset(
    {
        "claim_count",
        "lease_id",
    }
)

@dataclass(frozen=True)
class _ExpectedIndexSpec:
    """Structural contract for a required SQLite index on outbox_event."""

    name: str
    table: str
    columns: Tuple[str, ...]
    unique: bool = False
    partial: bool = False
    where_clause: Optional[str] = None


_OUTBOX_INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_outbox_status_available ON outbox_event(status, available_at_utc);",
    "CREATE INDEX IF NOT EXISTS idx_outbox_inflight_lease ON outbox_event(status, lease_expires_at_utc);",
    "CREATE INDEX IF NOT EXISTS idx_outbox_exec_attempt ON outbox_event(execution_id, attempt_id);",
    "CREATE INDEX IF NOT EXISTS idx_outbox_delivered ON outbox_event(status, delivered_at_utc);",
]

_OUTBOX_REQUIRED_INDEX_SPECS: Tuple[_ExpectedIndexSpec, ...] = (
    _ExpectedIndexSpec(
        name="idx_outbox_status_available",
        table="outbox_event",
        columns=("status", "available_at_utc"),
        unique=False,
        partial=False,
    ),
    _ExpectedIndexSpec(
        name="idx_outbox_inflight_lease",
        table="outbox_event",
        columns=("status", "lease_expires_at_utc"),
        unique=False,
        partial=False,
    ),
    _ExpectedIndexSpec(
        name="idx_outbox_exec_attempt",
        table="outbox_event",
        columns=("execution_id", "attempt_id"),
        unique=False,
        partial=False,
    ),
    _ExpectedIndexSpec(
        name="idx_outbox_delivered",
        table="outbox_event",
        columns=("status", "delivered_at_utc"),
        unique=False,
        partial=False,
    ),
)

_OUTBOX_REQUIRED_INDEXES = frozenset(spec.name for spec in _OUTBOX_REQUIRED_INDEX_SPECS)

_OUTBOX_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS outbox_event (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        execution_id TEXT,
        attempt_id TEXT,
        job_id TEXT,
        worker_id TEXT NOT NULL,
        fencing_token INTEGER CHECK (fencing_token IS NULL OR fencing_token >= 0),
        payload_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        available_at_utc TEXT NOT NULL,
        claim_count INTEGER NOT NULL DEFAULT 0 CHECK (claim_count >= 0),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        last_attempt_at_utc TEXT,
        delivered_at_utc TEXT,
        last_error TEXT,
        status TEXT NOT NULL,
        lease_id TEXT,
        lease_owner TEXT,
        lease_expires_at_utc TEXT,
        ack_id TEXT,
        schema_version INTEGER NOT NULL,
        checksum_sha256 TEXT NOT NULL
    );
    """,
    *_OUTBOX_INDEX_STATEMENTS,
]


# ---------------------------------------------------------------------------
# OutboxStore
# ---------------------------------------------------------------------------


class OutboxStore:
    """Durable SQLite Outbox sharing connection, locking, and transactions with StateStore."""

    def __init__(
        self,
        state_store: StateStore,
        *,
        retry_policy: Optional[RetryPolicy] = None,
        rng: Optional[Callable[[float, float], float]] = None,
    ) -> None:
        self._store = state_store
        self._retry_policy = retry_policy or RetryPolicy()
        self._rng = rng
        self._ensure_schema()

    @property
    def retry_policy(self) -> RetryPolicy:
        return self._retry_policy

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?;",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _get_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        return {
            r["name"]
            for r in conn.execute(f"PRAGMA table_info({table_name});").fetchall()
        }

    @staticmethod
    def _get_table_indexes(conn: sqlite3.Connection, table_name: str) -> Dict[str, sqlite3.Row]:
        return {
            r["name"]: r
            for r in conn.execute(f"PRAGMA index_list('{table_name}');").fetchall()
        }

    @staticmethod
    def _get_index_columns(conn: sqlite3.Connection, index_name: str) -> Tuple[Optional[str], ...]:
        rows = conn.execute(f"PRAGMA index_info('{index_name}');").fetchall()
        ordered = sorted(rows, key=lambda r: int(r["seqno"]))
        return tuple(r["name"] for r in ordered)

    @staticmethod
    def _extract_index_where_clause(conn: sqlite3.Connection, index_name: str) -> Optional[str]:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?;",
            (index_name,),
        ).fetchone()
        if row is None or not row["sql"]:
            return None
        sql_text = str(row["sql"])
        upper_sql = sql_text.upper()
        where_pos = upper_sql.find(" WHERE ")
        if where_pos == -1:
            return None
        return " ".join(sql_text[where_pos + 7 :].strip().rstrip(";").split())

    def _validate_v2_schema(self, conn: sqlite3.Connection) -> None:
        """Validate that outbox_event has all required v2 columns and structurally valid indexes."""
        existing_cols = self._get_table_columns(conn, "outbox_event")
        missing_cols = sorted(_OUTBOX_V2_REQUIRED_COLUMNS - existing_cols)
        if missing_cols:
            raise OutboxSchemaError(
                f"Inconsistent outbox_event schema (v{OUTBOX_SCHEMA_VERSION}): "
                f"missing required columns: {', '.join(missing_cols)}"
            )

        existing_indexes = self._get_table_indexes(conn, "outbox_event")
        missing_indexes = sorted(
            spec.name for spec in _OUTBOX_REQUIRED_INDEX_SPECS if spec.name not in existing_indexes
        )
        if missing_indexes:
            raise OutboxSchemaError(
                f"Inconsistent outbox_event schema (v{OUTBOX_SCHEMA_VERSION}): "
                f"missing required indexes: {', '.join(missing_indexes)}"
            )

        for spec in _OUTBOX_REQUIRED_INDEX_SPECS:
            idx_row = existing_indexes[spec.name]
            actual_unique = bool(idx_row["unique"])
            if actual_unique != spec.unique:
                raise OutboxSchemaError(
                    f"Inconsistent index '{spec.name}' on '{spec.table}': "
                    f"expected unique={spec.unique}, found unique={actual_unique}"
                )

            actual_partial = bool(idx_row["partial"])
            if actual_partial != spec.partial:
                raise OutboxSchemaError(
                    f"Inconsistent index '{spec.name}' on '{spec.table}': "
                    f"expected partial={spec.partial}, found partial={actual_partial}"
                )

            actual_cols = self._get_index_columns(conn, spec.name)
            if actual_cols != spec.columns:
                raise OutboxSchemaError(
                    f"Inconsistent index '{spec.name}' on '{spec.table}': "
                    f"expected columns {spec.columns}, found {actual_cols}"
                )

            if spec.partial and spec.where_clause is not None:
                actual_where = self._extract_index_where_clause(conn, spec.name)
                expected_where = " ".join(spec.where_clause.strip().split())
                if actual_where != expected_where:
                    raise OutboxSchemaError(
                        f"Inconsistent partial index '{spec.name}' on '{spec.table}': "
                        f"expected WHERE {expected_where!r}, found {actual_where!r}"
                    )

    def _ensure_schema(self) -> None:
        """Transactionally initialize, migrate, or validate the outbox_event schema.

        Explicitly distinguishes:
        - Scenario A (new DB): no outbox_event table and no outbox_schema_version metadata.
        - Scenario B (legacy v1): outbox_event exists with outbox_schema_version = 1.
        - Scenario C (current v2): outbox_event exists with outbox_schema_version = 2
          and all required columns and indexes are present.
        - Scenario D (inconsistent): any mismatch between table existence, metadata,
          columns, or indexes raises OutboxSchemaError without mutating the database.
        """
        with self._store._transaction() as conn:
            table_exists = self._table_exists(conn, "outbox_event")
            row = conn.execute(
                "SELECT value FROM schema_metadata WHERE key = ?;",
                (_OUTBOX_SCHEMA_META_KEY,),
            ).fetchone()

            if not table_exists and row is None:
                for stmt in _OUTBOX_SCHEMA_STATEMENTS:
                    conn.execute(stmt)
                self._validate_v2_schema(conn)
                conn.execute(
                    "INSERT INTO schema_metadata (key, value) VALUES (?, ?);",
                    (_OUTBOX_SCHEMA_META_KEY, str(OUTBOX_SCHEMA_VERSION)),
                )
                return

            if table_exists and row is None:
                raise OutboxSchemaError(
                    f"Inconsistent outbox schema: table 'outbox_event' exists without "
                    f"'{_OUTBOX_SCHEMA_META_KEY}' in schema_metadata"
                )

            if not table_exists and row is not None:
                raise OutboxSchemaError(
                    f"Inconsistent outbox schema: '{_OUTBOX_SCHEMA_META_KEY}'={row['value']!r} "
                    f"is present in schema_metadata, but table 'outbox_event' does not exist"
                )

            assert row is not None
            try:
                found_version = int(row["value"])
            except (TypeError, ValueError) as exc:
                raise OutboxSchemaError(
                    f"Invalid {_OUTBOX_SCHEMA_META_KEY} value: {row['value']}"
                ) from exc

            if found_version == 1:
                existing_cols = self._get_table_columns(conn, "outbox_event")
                missing_v1_cols = sorted(_OUTBOX_V1_REQUIRED_COLUMNS - existing_cols)
                if missing_v1_cols:
                    raise OutboxSchemaError(
                        f"Inconsistent legacy v1 outbox_event schema: "
                        f"missing required columns: {', '.join(missing_v1_cols)}"
                    )
                self._migrate_v1_to_v2(conn)
                self._validate_v2_schema(conn)
                return

            if found_version == OUTBOX_SCHEMA_VERSION:
                self._validate_v2_schema(conn)
                return

            raise OutboxSchemaError(
                f"Incompatible {_OUTBOX_SCHEMA_META_KEY}: "
                f"expected {OUTBOX_SCHEMA_VERSION}, found {found_version}"
            )

    def _migrate_v1_to_v2(self, conn: sqlite3.Connection) -> None:
        """Explicit transactional migration from Outbox schema v1 to v2.

        Adds `lease_id` and `claim_count`, preserves all existing events,
        payloads, history, and ACKs, and recomputes canonical v2 checksums for
        intact v1 records. Corrupted v1 records are preserved untouched so
        `verify_integrity()` continues to classify them as INCONCLUSIVE.
        """
        existing_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(outbox_event);").fetchall()
        }
        if "lease_id" not in existing_cols:
            conn.execute("ALTER TABLE outbox_event ADD COLUMN lease_id TEXT;")
        if "claim_count" not in existing_cols:
            conn.execute(
                "ALTER TABLE outbox_event ADD COLUMN claim_count INTEGER NOT NULL DEFAULT 0 CHECK (claim_count >= 0);"
            )

        rows = conn.execute("SELECT * FROM outbox_event;").fetchall()
        for row in rows:
            expected_v1 = compute_canonical_checksum(_outbox_checksum_payload_v1(row))
            if row["checksum_sha256"] != expected_v1:
                logger.error(
                    "Corrupted v1 outbox_event preserved untouched during v1->v2 migration",
                    extra={"event_id": row["event_id"], "classification": "INCONCLUSIVE"},
                )
                continue

            status = OutboxEventStatus(row["status"])
            attempt_count = int(row["attempt_count"])
            inferred_claims = max(
                attempt_count,
                1 if status == OutboxEventStatus.IN_FLIGHT else 0,
            )
            fencing_raw = row["fencing_token"]
            migrated = OutboxEventRecord(
                event_id=row["event_id"],
                event_type=row["event_type"],
                execution_id=row["execution_id"],
                attempt_id=row["attempt_id"],
                job_id=row["job_id"],
                worker_id=row["worker_id"],
                fencing_token=int(fencing_raw) if fencing_raw is not None else None,
                payload_json=row["payload_json"],
                created_at_utc=row["created_at_utc"],
                available_at_utc=row["available_at_utc"],
                claim_count=inferred_claims,
                attempt_count=attempt_count,
                last_attempt_at_utc=row["last_attempt_at_utc"],
                delivered_at_utc=row["delivered_at_utc"],
                last_error=row["last_error"],
                status=status,
                lease_id=None,
                lease_owner=row["lease_owner"],
                lease_expires_at_utc=row["lease_expires_at_utc"],
                ack_id=row["ack_id"],
                schema_version=OUTBOX_SCHEMA_VERSION,
            )
            new_checksum = compute_canonical_checksum(_outbox_checksum_payload(migrated))
            conn.execute(
                """
                UPDATE outbox_event
                SET lease_id = ?,
                    claim_count = ?,
                    schema_version = ?,
                    checksum_sha256 = ?
                WHERE event_id = ?;
                """,
                (
                    migrated.lease_id,
                    migrated.claim_count,
                    migrated.schema_version,
                    new_checksum,
                    migrated.event_id,
                ),
            )

        for idx_stmt in _OUTBOX_INDEX_STATEMENTS:
            conn.execute(idx_stmt)

        conn.execute(
            "UPDATE schema_metadata SET value = ? WHERE key = ?;",
            (str(OUTBOX_SCHEMA_VERSION), _OUTBOX_SCHEMA_META_KEY),
        )

    # -----------------------------------------------------------------------
    # Internal Validation Helpers
    # -----------------------------------------------------------------------

    def _validate_and_build_event(self, row: sqlite3.Row) -> OutboxEventRecord:
        if int(row["schema_version"]) != OUTBOX_SCHEMA_VERSION:
            raise OutboxCorruptionError(
                f"Incompatible outbox_event schema_version={row['schema_version']} for {row['event_id']}"
            )
        try:
            status = OutboxEventStatus(row["status"])
        except ValueError as exc:
            raise OutboxCorruptionError(
                f"Invalid outbox status '{row['status']}' for {row['event_id']}"
            ) from exc

        # Validate payload_json is still a valid JSON object
        try:
            parsed_payload = json.loads(row["payload_json"])
            if not isinstance(parsed_payload, dict):
                raise OutboxCorruptionError(
                    f"Corrupted payload_json (not a JSON object) for {row['event_id']}"
                )
        except json.JSONDecodeError as exc:
            raise OutboxCorruptionError(
                f"Corrupted payload_json for {row['event_id']}: {exc}"
            ) from exc

        fencing_raw = row["fencing_token"]
        fencing_token = int(fencing_raw) if fencing_raw is not None else None

        rec = OutboxEventRecord(
            event_id=row["event_id"],
            event_type=row["event_type"],
            execution_id=row["execution_id"],
            attempt_id=row["attempt_id"],
            job_id=row["job_id"],
            worker_id=row["worker_id"],
            fencing_token=fencing_token,
            payload_json=row["payload_json"],
            created_at_utc=row["created_at_utc"],
            available_at_utc=row["available_at_utc"],
            claim_count=int(row["claim_count"]),
            attempt_count=int(row["attempt_count"]),
            last_attempt_at_utc=row["last_attempt_at_utc"],
            delivered_at_utc=row["delivered_at_utc"],
            last_error=row["last_error"],
            status=status,
            lease_id=row["lease_id"],
            lease_owner=row["lease_owner"],
            lease_expires_at_utc=row["lease_expires_at_utc"],
            ack_id=row["ack_id"],
            schema_version=int(row["schema_version"]),
            checksum_sha256=row["checksum_sha256"],
        )
        expected = compute_canonical_checksum(_outbox_checksum_payload(rec))
        if rec.checksum_sha256 != expected:
            logger.error(
                "Outbox corruption detected in outbox_event",
                extra={"event_id": rec.event_id, "classification": "INCONCLUSIVE"},
            )
            raise OutboxCorruptionError(
                f"Checksum mismatch for outbox_event {rec.event_id} (classified INCONCLUSIVE)"
            )
        return rec

    @staticmethod
    def _validate_transition(current: OutboxEventStatus, target: OutboxEventStatus) -> None:
        allowed = _VALID_TRANSITIONS.get(current, ())
        if target not in allowed:
            raise OutboxStateTransitionError(
                f"Invalid outbox status transition: {current.value} -> {target.value}"
            )

    @staticmethod
    def _validate_fencing_token(
        record: OutboxEventRecord,
        expected_fencing_token: Optional[int],
        *,
        strict: bool = False,
    ) -> None:
        """Validate fencing_token against `record`.

        When `strict=True` (used for all IN_FLIGHT mutations):
        - If `record.fencing_token is not None`: `expected_fencing_token` MUST be
          provided and match `record.fencing_token` exactly.
        - If `record.fencing_token is None`: `expected_fencing_token` MUST be `None`
          (explicit rule for unfenced events; never invents or assigns a token).
        """
        if expected_fencing_token is not None and expected_fencing_token < 0:
            raise OutboxConflictError("fencing_token must be non-negative")

        if record.fencing_token is None:
            if strict and expected_fencing_token is not None:
                raise OutboxConflictError(
                    f"Unexpected fencing_token {expected_fencing_token} for unfenced "
                    f"outbox_event {record.event_id}"
                )
            return

        if expected_fencing_token is None:
            if strict:
                raise OutboxConflictError(
                    f"Missing expected_fencing_token for fenced outbox_event {record.event_id}"
                )
            return

        if expected_fencing_token < record.fencing_token:
            raise OutboxConflictError(
                f"Stale fencing_token {expected_fencing_token} < {record.fencing_token} "
                f"for outbox_event {record.event_id}"
            )
        if expected_fencing_token != record.fencing_token:
            raise OutboxConflictError(
                f"Fencing token mismatch for outbox_event {record.event_id}: "
                f"expected {record.fencing_token}, got {expected_fencing_token}"
            )

    @classmethod
    def _validate_lease_ownership(
        cls,
        record: OutboxEventRecord,
        *,
        lease_owner: Optional[str],
        lease_id: Optional[str],
        expected_fencing_token: Optional[int],
        now_iso: str,
    ) -> None:
        """Strictly validate IN_FLIGHT lease ownership, expiration, and fencing token."""
        if not lease_owner or not isinstance(lease_owner, str) or not lease_owner.strip():
            logger.warning(
                "Rejected outbox mutation: missing or empty lease_owner",
                extra={"event_id": record.event_id},
            )
            raise OutboxConflictError(
                f"Missing required lease_owner for outbox_event '{record.event_id}'"
            )
        if not lease_id or not isinstance(lease_id, str) or not lease_id.strip():
            logger.warning(
                "Rejected outbox mutation: missing or empty lease_id",
                extra={"event_id": record.event_id, "lease_owner": lease_owner},
            )
            raise OutboxConflictError(
                f"Missing required lease_id for outbox_event '{record.event_id}'"
            )

        if record.status != OutboxEventStatus.IN_FLIGHT:
            logger.warning(
                "Rejected outbox mutation: event is not IN_FLIGHT",
                extra={
                    "event_id": record.event_id,
                    "status": record.status.value,
                    "lease_owner": lease_owner,
                    "lease_id": lease_id,
                },
            )
            raise OutboxStateTransitionError(
                f"Outbox event '{record.event_id}' is not IN_FLIGHT (current status: {record.status.value})"
            )

        if record.lease_owner != lease_owner:
            logger.warning(
                "Rejected outbox mutation: lease_owner mismatch",
                extra={
                    "event_id": record.event_id,
                    "expected_lease_owner": record.lease_owner,
                    "provided_lease_owner": lease_owner,
                },
            )
            raise OutboxConflictError(
                f"Lease owner mismatch for event '{record.event_id}': "
                f"expected '{record.lease_owner}', got '{lease_owner}'"
            )

        if record.lease_id is None or record.lease_id != lease_id:
            logger.warning(
                "Rejected outbox mutation: lease_id mismatch",
                extra={
                    "event_id": record.event_id,
                    "expected_lease_id": record.lease_id,
                    "provided_lease_id": lease_id,
                },
            )
            raise OutboxConflictError(
                f"Lease ID mismatch for event '{record.event_id}': "
                f"expected '{record.lease_id}', got '{lease_id}'"
            )

        if record.lease_expires_at_utc is None or record.lease_expires_at_utc <= now_iso:
            logger.warning(
                "Rejected outbox mutation: lease expired",
                extra={
                    "event_id": record.event_id,
                    "lease_id": lease_id,
                    "lease_expires_at_utc": record.lease_expires_at_utc,
                    "now_utc": now_iso,
                },
            )
            raise OutboxConflictError(
                f"Lease expired for event '{record.event_id}' "
                f"(expired_at={record.lease_expires_at_utc}, now={now_iso})"
            )

        cls._validate_fencing_token(record, expected_fencing_token, strict=True)

    @staticmethod
    def _persist_update(conn: sqlite3.Connection, updated: OutboxEventRecord) -> OutboxEventRecord:
        checksum = compute_canonical_checksum(_outbox_checksum_payload(updated))
        final_rec = OutboxEventRecord(**{**updated.__dict__, "checksum_sha256": checksum})
        conn.execute(
            """
            UPDATE outbox_event
            SET available_at_utc = ?,
                claim_count = ?,
                attempt_count = ?,
                last_attempt_at_utc = ?,
                delivered_at_utc = ?,
                last_error = ?,
                status = ?,
                lease_id = ?,
                lease_owner = ?,
                lease_expires_at_utc = ?,
                ack_id = ?,
                schema_version = ?,
                checksum_sha256 = ?
            WHERE event_id = ?;
            """,
            (
                final_rec.available_at_utc,
                final_rec.claim_count,
                final_rec.attempt_count,
                final_rec.last_attempt_at_utc,
                final_rec.delivered_at_utc,
                final_rec.last_error,
                final_rec.status.value,
                final_rec.lease_id,
                final_rec.lease_owner,
                final_rec.lease_expires_at_utc,
                final_rec.ack_id,
                final_rec.schema_version,
                final_rec.checksum_sha256,
                final_rec.event_id,
            ),
        )
        return final_rec

    # -----------------------------------------------------------------------
    # Enqueue
    # -----------------------------------------------------------------------

    def enqueue_event(
        self,
        *,
        event_type: str,
        worker_id: str,
        payload: Any,
        event_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        job_id: Optional[str] = None,
        fencing_token: Optional[int] = None,
        available_at_utc: Optional[str] = None,
        now_utc: Optional[datetime] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> OutboxEventRecord:
        """Persist an outbox event durably before any external delivery attempt.

        If `conn` is provided, participates in the caller's active SQLite
        transaction (enabling atomic local business state + outbox writes).
        """
        if not event_type or not str(event_type).strip():
            raise OutboxPayloadError("event_type must be a non-empty string")
        if not worker_id or not str(worker_id).strip():
            raise OutboxPayloadError("worker_id must be a non-empty string")
        if fencing_token is not None and fencing_token < 0:
            raise OutboxConflictError("fencing_token must be non-negative")

        stable_event_id = event_id.strip() if event_id else str(uuid.uuid4())
        if not stable_event_id:
            raise OutboxPayloadError("event_id must be non-empty when provided")

        canonical_payload = _canonicalize_payload(payload)
        now_iso = format_utc_iso(now_utc or utc_now())
        avail_iso = available_at_utc or now_iso

        def _do_enqueue(active_conn: sqlite3.Connection) -> OutboxEventRecord:
            existing_row = active_conn.execute(
                "SELECT * FROM outbox_event WHERE event_id = ?;",
                (stable_event_id,),
            ).fetchone()
            if existing_row is not None:
                existing = self._validate_and_build_event(existing_row)
                same_content = (
                    existing.event_type == event_type
                    and existing.execution_id == execution_id
                    and existing.attempt_id == attempt_id
                    and existing.job_id == job_id
                    and existing.worker_id == worker_id
                    and existing.fencing_token == fencing_token
                    and existing.payload_json == canonical_payload
                )
                if same_content:
                    return existing
                raise OutboxConflictError(
                    f"Duplicate event_id '{stable_event_id}' with conflicting content"
                )

            rec = OutboxEventRecord(
                event_id=stable_event_id,
                event_type=event_type,
                execution_id=execution_id,
                attempt_id=attempt_id,
                job_id=job_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                payload_json=canonical_payload,
                created_at_utc=now_iso,
                available_at_utc=avail_iso,
                claim_count=0,
                attempt_count=0,
                last_attempt_at_utc=None,
                delivered_at_utc=None,
                last_error=None,
                status=OutboxEventStatus.PENDING,
                lease_id=None,
                lease_owner=None,
                lease_expires_at_utc=None,
                ack_id=None,
                schema_version=OUTBOX_SCHEMA_VERSION,
            )
            checksum = compute_canonical_checksum(_outbox_checksum_payload(rec))
            rec = OutboxEventRecord(**{**rec.__dict__, "checksum_sha256": checksum})

            active_conn.execute(
                """
                INSERT INTO outbox_event (
                    event_id, event_type, execution_id, attempt_id, job_id,
                    worker_id, fencing_token, payload_json, created_at_utc,
                    available_at_utc, claim_count, attempt_count, last_attempt_at_utc,
                    delivered_at_utc, last_error, status, lease_id, lease_owner,
                    lease_expires_at_utc, ack_id, schema_version, checksum_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    rec.event_id,
                    rec.event_type,
                    rec.execution_id,
                    rec.attempt_id,
                    rec.job_id,
                    rec.worker_id,
                    rec.fencing_token,
                    rec.payload_json,
                    rec.created_at_utc,
                    rec.available_at_utc,
                    rec.claim_count,
                    rec.attempt_count,
                    rec.last_attempt_at_utc,
                    rec.delivered_at_utc,
                    rec.last_error,
                    rec.status.value,
                    rec.lease_id,
                    rec.lease_owner,
                    rec.lease_expires_at_utc,
                    rec.ack_id,
                    rec.schema_version,
                    rec.checksum_sha256,
                ),
            )
            return rec

        if conn is not None:
            return _do_enqueue(conn)
        with self._store._transaction() as tx_conn:
            return _do_enqueue(tx_conn)

    # -----------------------------------------------------------------------
    # Queries
    # -----------------------------------------------------------------------

    def get_event(self, event_id: str) -> Optional[OutboxEventRecord]:
        """Retrieve and validate a single outbox event by event_id."""
        with self._store._lock:
            conn = self._store._require_conn()
            row = conn.execute(
                "SELECT * FROM outbox_event WHERE event_id = ?;",
                (event_id,),
            ).fetchone()
            if row is None:
                return None
            return self._validate_and_build_event(row)

    def get_pending_events(
        self,
        *,
        limit: int = 50,
        now_utc: Optional[datetime] = None,
    ) -> List[OutboxEventRecord]:
        """Return PENDING or RETRY events available for delivery at `now_utc`."""
        if limit < 1:
            return []
        now_iso = format_utc_iso(now_utc or utc_now())
        with self._store._lock:
            conn = self._store._require_conn()
            rows = conn.execute(
                """
                SELECT * FROM outbox_event
                WHERE status IN (?, ?)
                  AND available_at_utc <= ?
                ORDER BY available_at_utc ASC, created_at_utc ASC
                LIMIT ?;
                """,
                (
                    OutboxEventStatus.PENDING.value,
                    OutboxEventStatus.RETRY.value,
                    now_iso,
                    limit,
                ),
            ).fetchall()
            return [self._validate_and_build_event(r) for r in rows]

    # -----------------------------------------------------------------------
    # Claim & Lease Recovery
    # -----------------------------------------------------------------------

    def claim_event(
        self,
        lease_owner: str,
        *,
        lease_duration_seconds: float = 30.0,
        event_id: Optional[str] = None,
        expected_fencing_token: Optional[int] = None,
        now_utc: Optional[datetime] = None,
    ) -> Optional[OutboxEventRecord]:
        """Atomically claim a single available event with a timed lease.

        The SQLite transaction is committed before returning so no database
        lock is held during external HTTP calls.
        """
        if not lease_owner or not lease_owner.strip():
            raise OutboxPayloadError("lease_owner must be a non-empty string")
        if lease_duration_seconds <= 0:
            raise ValueError("lease_duration_seconds must be > 0")

        now_dt = now_utc or utc_now()
        now_iso = format_utc_iso(now_dt)
        lease_exp_iso = format_utc_iso(now_dt + timedelta(seconds=lease_duration_seconds))

        with self._store._transaction() as conn:
            current: Optional[OutboxEventRecord] = None
            if event_id is not None:
                row = conn.execute(
                    "SELECT * FROM outbox_event WHERE event_id = ?;",
                    (event_id,),
                ).fetchone()
                if row is None:
                    return None
                try:
                    candidate = self._validate_and_build_event(row)
                except (OutboxCorruptionError, StateStoreCorruptionError) as exc:
                    logger.error(
                        "Skipping corrupted outbox_event during explicit claim; "
                        "classified INCONCLUSIVE and preserved untouched",
                        extra={
                            "event_id": row["event_id"],
                            "classification": "INCONCLUSIVE",
                            "error": str(exc),
                        },
                    )
                    return None
                self._validate_fencing_token(candidate, expected_fencing_token)
                if candidate.status not in (OutboxEventStatus.PENDING, OutboxEventStatus.RETRY):
                    return None
                if candidate.available_at_utc > now_iso:
                    return None
                current = candidate
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM outbox_event
                    WHERE status IN (?, ?)
                      AND available_at_utc <= ?
                    ORDER BY available_at_utc ASC, created_at_utc ASC, event_id ASC;
                    """,
                    (
                        OutboxEventStatus.PENDING.value,
                        OutboxEventStatus.RETRY.value,
                        now_iso,
                    ),
                ).fetchall()
                for row in rows:
                    try:
                        candidate = self._validate_and_build_event(row)
                    except (OutboxCorruptionError, StateStoreCorruptionError) as exc:
                        logger.error(
                            "Skipping corrupted outbox_event during claim_event; "
                            "classified INCONCLUSIVE and preserved untouched",
                            extra={
                                "event_id": row["event_id"],
                                "classification": "INCONCLUSIVE",
                                "error": str(exc),
                            },
                        )
                        continue
                    self._validate_fencing_token(candidate, expected_fencing_token)
                    current = candidate
                    break

            if current is None:
                return None

            self._validate_transition(current.status, OutboxEventStatus.IN_FLIGHT)
            new_lease_id = str(uuid.uuid4())
            updated = OutboxEventRecord(
                **{
                    **current.__dict__,
                    "status": OutboxEventStatus.IN_FLIGHT,
                    "claim_count": current.claim_count + 1,
                    "lease_id": new_lease_id,
                    "lease_owner": lease_owner,
                    "lease_expires_at_utc": lease_exp_iso,
                    "last_attempt_at_utc": now_iso,
                }
            )
            return self._persist_update(conn, updated)

    def recover_expired_leases(
        self,
        *,
        now_utc: Optional[datetime] = None,
    ) -> List[OutboxEventRecord]:
        """Recover abandoned IN_FLIGHT events whose lease has expired.

        Semantics:
        - Counts each expired lease as an abandoned attempt toward `max_attempts`.
        - Never marks an event DELIVERED and never assumes Cloud Hub did not receive it
          (preserves at-least-once delivery with Hub-side deduplication by `event_id`).
        - Moves events to DEAD_LETTER once `max_attempts` is reached.
        - Corrupted rows are NOT recovered automatically; they are logged and preserved
          as INCONCLUSIVE evidence.
        """
        now_iso = format_utc_iso(now_utc or utc_now())
        recovered: List[OutboxEventRecord] = []

        with self._store._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM outbox_event
                WHERE status = ?
                  AND lease_expires_at_utc IS NOT NULL
                  AND lease_expires_at_utc <= ?
                ORDER BY lease_expires_at_utc ASC;
                """,
                (OutboxEventStatus.IN_FLIGHT.value, now_iso),
            ).fetchall()

            for row in rows:
                try:
                    current = self._validate_and_build_event(row)
                except (OutboxCorruptionError, StateStoreCorruptionError) as exc:
                    logger.error(
                        "Skipping corrupted IN_FLIGHT outbox_event during lease recovery; "
                        "classified INCONCLUSIVE and preserved as evidence",
                        extra={
                            "event_id": row["event_id"],
                            "classification": "INCONCLUSIVE",
                            "error": str(exc),
                        },
                    )
                    continue

                new_attempt_count = current.attempt_count + 1
                if new_attempt_count >= self._retry_policy.max_attempts:
                    target_status = OutboxEventStatus.DEAD_LETTER
                    next_available_iso = current.available_at_utc
                    error_msg = (
                        f"Abandoned lease expired (lease_id={current.lease_id}, "
                        f"lease_owner={current.lease_owner}); "
                        f"max_attempts ({self._retry_policy.max_attempts}) reached"
                    )
                else:
                    target_status = OutboxEventStatus.RETRY
                    next_available_iso = now_iso
                    error_msg = (
                        f"Abandoned lease expired (lease_id={current.lease_id}, "
                        f"lease_owner={current.lease_owner}); "
                        f"outcome unknown, scheduled for at-least-once retry"
                    )

                self._validate_transition(current.status, target_status)
                updated = OutboxEventRecord(
                    **{
                        **current.__dict__,
                        "status": target_status,
                        "attempt_count": new_attempt_count,
                        "last_error": error_msg,
                        "lease_id": None,
                        "lease_owner": None,
                        "lease_expires_at_utc": None,
                        "available_at_utc": next_available_iso,
                    }
                )
                recovered.append(self._persist_update(conn, updated))

        return recovered

    # -----------------------------------------------------------------------
    # Delivery Outcome Transitions
    # -----------------------------------------------------------------------

    def mark_delivered(
        self,
        event_id: str,
        ack: Optional[HubAckResponse],
        *,
        lease_owner: str,
        lease_id: str,
        expected_fencing_token: Optional[int] = None,
        now_utc: Optional[datetime] = None,
    ) -> OutboxEventRecord:
        """Confirm delivery only upon a valid, unambiguous Cloud Hub ACK and active lease."""
        if ack is None or not isinstance(ack, HubAckResponse):
            raise OutboxAckError("Invalid or missing HubAckResponse")
        if ack.accepted is not True:
            raise OutboxAckError(
                f"Ambiguous or unaccepted ACK for event '{event_id}' (accepted={ack.accepted!r})"
            )
        if not ack.event_id or ack.event_id != event_id:
            raise OutboxAckError(
                f"ACK event_id mismatch: expected '{event_id}', got '{ack.event_id}'"
            )
        if not lease_owner or not isinstance(lease_owner, str) or not lease_owner.strip():
            raise OutboxConflictError(
                f"Missing required lease_owner for outbox_event '{event_id}'"
            )
        if not lease_id or not isinstance(lease_id, str) or not lease_id.strip():
            raise OutboxConflictError(
                f"Missing required lease_id for outbox_event '{event_id}'"
            )

        now_iso = format_utc_iso(now_utc or utc_now())
        with self._store._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM outbox_event WHERE event_id = ?;",
                (event_id,),
            ).fetchone()
            if row is None:
                raise StateStorePersistenceError(f"Outbox event '{event_id}' not found")

            current = self._validate_and_build_event(row)

            # Idempotent repeated ACK for an already delivered event (no mutation)
            if current.status == OutboxEventStatus.DELIVERED:
                self._validate_fencing_token(current, expected_fencing_token, strict=True)
                return current

            self._validate_lease_ownership(
                current,
                lease_owner=lease_owner,
                lease_id=lease_id,
                expected_fencing_token=expected_fencing_token,
                now_iso=now_iso,
            )

            self._validate_transition(current.status, OutboxEventStatus.DELIVERED)
            updated = OutboxEventRecord(
                **{
                    **current.__dict__,
                    "status": OutboxEventStatus.DELIVERED,
                    "delivered_at_utc": now_iso,
                    "lease_id": None,
                    "lease_owner": None,
                    "lease_expires_at_utc": None,
                    "ack_id": ack.ack_id,
                    "last_error": None,
                }
            )
            return self._persist_update(conn, updated)

    def mark_retry(
        self,
        event_id: str,
        error_message: str,
        *,
        lease_owner: str,
        lease_id: str,
        expected_fencing_token: Optional[int] = None,
        now_utc: Optional[datetime] = None,
    ) -> OutboxEventRecord:
        """Record a temporary delivery failure and schedule exponential backoff.

        Requires valid IN_FLIGHT lease ownership. Transitions to DEAD_LETTER
        automatically when `max_attempts` is reached.
        """
        now_dt = now_utc or utc_now()
        now_iso = format_utc_iso(now_dt)

        with self._store._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM outbox_event WHERE event_id = ?;",
                (event_id,),
            ).fetchone()
            if row is None:
                raise StateStorePersistenceError(f"Outbox event '{event_id}' not found")

            current = self._validate_and_build_event(row)
            self._validate_lease_ownership(
                current,
                lease_owner=lease_owner,
                lease_id=lease_id,
                expected_fencing_token=expected_fencing_token,
                now_iso=now_iso,
            )

            new_attempt_count = current.attempt_count + 1
            if new_attempt_count >= self._retry_policy.max_attempts:
                target_status = OutboxEventStatus.DEAD_LETTER
                next_available_iso = current.available_at_utc
            else:
                target_status = OutboxEventStatus.RETRY
                delay = self._retry_policy.compute_delay_seconds(new_attempt_count, rng=self._rng)
                next_available_iso = format_utc_iso(now_dt + timedelta(seconds=delay))

            self._validate_transition(current.status, target_status)
            updated = OutboxEventRecord(
                **{
                    **current.__dict__,
                    "status": target_status,
                    "attempt_count": new_attempt_count,
                    "last_attempt_at_utc": now_iso,
                    "available_at_utc": next_available_iso,
                    "last_error": str(error_message),
                    "lease_id": None,
                    "lease_owner": None,
                    "lease_expires_at_utc": None,
                }
            )
            return self._persist_update(conn, updated)

    def mark_dead_letter(
        self,
        event_id: str,
        error_message: str,
        *,
        lease_owner: str,
        lease_id: str,
        expected_fencing_token: Optional[int] = None,
        now_utc: Optional[datetime] = None,
    ) -> OutboxEventRecord:
        """Move an IN_FLIGHT event to DEAD_LETTER on permanent failure, preserving all history."""
        now_iso = format_utc_iso(now_utc or utc_now())

        with self._store._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM outbox_event WHERE event_id = ?;",
                (event_id,),
            ).fetchone()
            if row is None:
                raise StateStorePersistenceError(f"Outbox event '{event_id}' not found")

            current = self._validate_and_build_event(row)
            self._validate_lease_ownership(
                current,
                lease_owner=lease_owner,
                lease_id=lease_id,
                expected_fencing_token=expected_fencing_token,
                now_iso=now_iso,
            )

            self._validate_transition(current.status, OutboxEventStatus.DEAD_LETTER)
            new_attempt_count = current.attempt_count + 1
            updated = OutboxEventRecord(
                **{
                    **current.__dict__,
                    "status": OutboxEventStatus.DEAD_LETTER,
                    "attempt_count": new_attempt_count,
                    "last_attempt_at_utc": now_iso,
                    "last_error": str(error_message),
                    "lease_id": None,
                    "lease_owner": None,
                    "lease_expires_at_utc": None,
                }
            )
            return self._persist_update(conn, updated)

    # -----------------------------------------------------------------------
    # Delivery Orchestration (Outside SQLite Transaction)
    # -----------------------------------------------------------------------

    def deliver_claimed_event(
        self,
        event: OutboxEventRecord,
        transport: OutboxTransport,
        *,
        now_utc: Optional[datetime] = None,
    ) -> OutboxEventRecord:
        """Deliver an already-claimed IN_FLIGHT event using `transport`.

        SQLite is NOT held open during `transport.send_event(...)`.
        """
        if event.status != OutboxEventStatus.IN_FLIGHT:
            raise OutboxStateTransitionError(
                f"deliver_claimed_event requires IN_FLIGHT event, got {event.status.value}"
            )
        if not event.lease_owner or not event.lease_id:
            raise OutboxConflictError(
                f"deliver_claimed_event requires valid lease_owner and lease_id for '{event.event_id}'"
            )

        try:
            ack = transport.send_event(event, idempotency_key=event.event_id)
        except OutboxPermanentDeliveryError as exc:
            return self.mark_dead_letter(
                event.event_id,
                f"Permanent delivery error: {exc}",
                lease_owner=event.lease_owner,
                lease_id=event.lease_id,
                expected_fencing_token=event.fencing_token,
                now_utc=now_utc,
            )
        except (OutboxTemporaryDeliveryError, TimeoutError, ConnectionError, OSError) as exc:
            return self.mark_retry(
                event.event_id,
                f"Temporary delivery error: {exc}",
                lease_owner=event.lease_owner,
                lease_id=event.lease_id,
                expected_fencing_token=event.fencing_token,
                now_utc=now_utc,
            )
        except Exception as exc:
            return self.mark_retry(
                event.event_id,
                f"Unexpected delivery error: {exc}",
                lease_owner=event.lease_owner,
                lease_id=event.lease_id,
                expected_fencing_token=event.fencing_token,
                now_utc=now_utc,
            )

        if ack is None or not isinstance(ack, HubAckResponse):
            return self.mark_retry(
                event.event_id,
                "Ambiguous transport response: missing HubAckResponse",
                lease_owner=event.lease_owner,
                lease_id=event.lease_id,
                expected_fencing_token=event.fencing_token,
                now_utc=now_utc,
            )

        if ack.permanent_rejection or (
            ack.http_status is not None and 400 <= ack.http_status < 500 and ack.http_status != 429
        ):
            return self.mark_dead_letter(
                event.event_id,
                ack.error_message or f"Permanent HTTP rejection (status={ack.http_status})",
                lease_owner=event.lease_owner,
                lease_id=event.lease_id,
                expected_fencing_token=event.fencing_token,
                now_utc=now_utc,
            )

        if ack.http_status is not None and (ack.http_status == 429 or ack.http_status >= 500):
            return self.mark_retry(
                event.event_id,
                ack.error_message or f"Temporary HTTP error (status={ack.http_status})",
                lease_owner=event.lease_owner,
                lease_id=event.lease_id,
                expected_fencing_token=event.fencing_token,
                now_utc=now_utc,
            )

        try:
            return self.mark_delivered(
                event.event_id,
                ack,
                lease_owner=event.lease_owner,
                lease_id=event.lease_id,
                expected_fencing_token=event.fencing_token,
                now_utc=now_utc,
            )
        except OutboxAckError as exc:
            return self.mark_retry(
                event.event_id,
                f"Invalid/ambiguous ACK: {exc}",
                lease_owner=event.lease_owner,
                lease_id=event.lease_id,
                expected_fencing_token=event.fencing_token,
                now_utc=now_utc,
            )

    # -----------------------------------------------------------------------
    # Integrity Verification
    # -----------------------------------------------------------------------

    def verify_integrity(self) -> List[IntegrityVerificationReport]:
        """Scan all outbox_event rows and classify corrupted ones as INCONCLUSIVE."""
        reports: List[IntegrityVerificationReport] = []
        with self._store._lock:
            conn = self._store._require_conn()
            for row in conn.execute("SELECT * FROM outbox_event;").fetchall():
                key = str(row["event_id"])
                try:
                    self._validate_and_build_event(row)
                    reports.append(
                        IntegrityVerificationReport(IntegrityStatus.VALID, "outbox_event", key)
                    )
                except (
                    OutboxCorruptionError,
                    StateStoreCorruptionError,
                    ValueError,
                    TypeError,
                    KeyError,
                    sqlite3.Error,
                ) as exc:
                    reports.append(
                        IntegrityVerificationReport(
                            IntegrityStatus.INCONCLUSIVE,
                            "outbox_event",
                            key,
                            reason=str(exc),
                            raw_diagnostic=dict(row),
                        )
                    )
        return reports
