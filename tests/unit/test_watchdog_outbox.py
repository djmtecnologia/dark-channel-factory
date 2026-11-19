"""Unit tests for Watchdog Durable Outbox (Sprint 2.7 - Task 4)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from desktop_worker.watchdog.identity import CanonicalExecutionIdentity
from desktop_worker.watchdog.outbox import (
    OUTBOX_SCHEMA_VERSION,
    HubAckResponse,
    OutboxAckError,
    OutboxConflictError,
    OutboxCorruptionError,
    OutboxEventRecord,
    OutboxEventStatus,
    OutboxPayloadError,
    OutboxPermanentDeliveryError,
    OutboxSchemaError,
    OutboxStateTransitionError,
    OutboxStore,
    OutboxTemporaryDeliveryError,
    RetryPolicy,
    parse_utc_iso,
)
from desktop_worker.watchdog.state_store import (
    IntegrityStatus,
    StateStore,
    StateStorePersistenceError,
)


class _MockTransport:
    """Configurable mock Cloud Hub transport for unit tests."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: List[Dict[str, str]] = []

    def send_event(self, event: OutboxEventRecord, *, idempotency_key: str) -> HubAckResponse:
        self.calls.append({"event_id": event.event_id, "idempotency_key": idempotency_key})
        return self._handler(event, idempotency_key)


# ---------------------------------------------------------------------------
# Persistence & Integrity Tests
# ---------------------------------------------------------------------------


def test_enqueue_and_recover_after_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        created = outbox.enqueue_event(
            event_id="evt-001",
            event_type="TERMINATION_CONFIRMED",
            worker_id="worker-1",
            execution_id="exec-1",
            attempt_id="att-1",
            job_id="job-1",
            fencing_token=10,
            payload={"reason": "COMPLETED", "exit_code": 0},
        )
        assert created.event_id == "evt-001"
        assert created.status == OutboxEventStatus.PENDING
        assert created.attempt_count == 0
        assert len(created.checksum_sha256) == 64

    with StateStore(db_path) as reopened_store:
        reopened_outbox = OutboxStore(reopened_store)
        fetched = reopened_outbox.get_event("evt-001")
        assert fetched is not None
        assert fetched == created
        pending = reopened_outbox.get_pending_events()
        assert len(pending) == 1
        assert pending[0].event_id == "evt-001"


def test_invalid_payload_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        with pytest.raises(OutboxPayloadError):
            outbox.enqueue_event(
                event_type="TEST",
                worker_id="worker-1",
                payload="{not-valid-json",
            )
        with pytest.raises(OutboxPayloadError):
            outbox.enqueue_event(
                event_type="TEST",
                worker_id="worker-1",
                payload=["not", "a", "dict"],
            )
        with pytest.raises(OutboxPayloadError):
            outbox.enqueue_event(
                event_type="",
                worker_id="worker-1",
                payload={"ok": True},
            )


def test_duplicate_event_same_content_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        first = outbox.enqueue_event(
            event_id="evt-dup-1",
            event_type="HEARTBEAT",
            worker_id="worker-1",
            fencing_token=3,
            payload={"b": 2, "a": 1},
        )
        second = outbox.enqueue_event(
            event_id="evt-dup-1",
            event_type="HEARTBEAT",
            worker_id="worker-1",
            fencing_token=3,
            payload='{"a":1,"b":2}',
        )
        assert second == first


def test_duplicate_event_divergent_content_raises_conflict(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-dup-2",
            event_type="HEARTBEAT",
            worker_id="worker-1",
            fencing_token=3,
            payload={"status": "RUNNING"},
        )
        with pytest.raises(OutboxConflictError, match="conflicting content"):
            outbox.enqueue_event(
                event_id="evt-dup-2",
                event_type="HEARTBEAT",
                worker_id="worker-1",
                fencing_token=3,
                payload={"status": "CLOSED"},
            )


def test_corrupted_checksum_detected_and_verify_integrity(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-valid",
            event_type="HEARTBEAT",
            worker_id="worker-1",
            payload={"ok": True},
        )
        outbox.enqueue_event(
            event_id="evt-corrupt",
            event_type="HEARTBEAT",
            worker_id="worker-1",
            payload={"ok": True},
        )

    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE outbox_event SET status = 'DELIVERED' WHERE event_id = 'evt-corrupt';")
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        with pytest.raises(OutboxCorruptionError, match="INCONCLUSIVE"):
            outbox.get_event("evt-corrupt")

        reports = outbox.verify_integrity()
        by_key = {r.record_key: r for r in reports}
        assert by_key["evt-valid"].status == IntegrityStatus.VALID
        assert by_key["evt-corrupt"].status == IntegrityStatus.INCONCLUSIVE
        assert by_key["evt-corrupt"].raw_diagnostic is not None
        assert by_key["evt-corrupt"].raw_diagnostic["status"] == "DELIVERED"


def test_schema_migration_preserves_existing_task3_data_and_validates_version(tmp_path: Path) -> None:
    db_path = tmp_path / "existing_task3.db"
    ident = CanonicalExecutionIdentity(
        execution_id="exec-legacy",
        attempt_id="att-legacy",
        job_id="job-legacy",
        tenant_id="t1",
        channel_id="c1",
        worker_id="w1",
        fencing_token=5,
        pid=1234,
        process_create_time=133750000001234567,
        job_object_name="Local\\DCF_UPLOAD_att-legacy_exec-legacy",
        browser_user_data_dir="C:\\profiles\\c1",
    )
    with StateStore(db_path) as store:
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")

    # Initialize Outbox on existing Task 3 DB
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-migrated",
            event_type="EXECUTION_STARTED",
            worker_id="w1",
            execution_id="exec-legacy",
            payload={"ok": True},
        )
        assert store.get_execution("exec-legacy") is not None

    # Tamper outbox_schema_version to incompatible value
    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE schema_metadata SET value = '999' WHERE key = 'outbox_schema_version';")
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        with pytest.raises(OutboxSchemaError, match="Incompatible outbox_schema_version"):
            OutboxStore(store)


# ---------------------------------------------------------------------------
# Concurrency, Lease & Fencing Tests
# ---------------------------------------------------------------------------


def test_atomic_claim_prevents_two_workers_claiming_same_event(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-race",
            event_type="TERMINATION",
            worker_id="worker-1",
            payload={"seq": 1},
        )

        results: List[Optional[OutboxEventRecord]] = []
        barrier = threading.Barrier(2)

        def _worker(owner: str) -> None:
            barrier.wait()
            claimed = outbox.claim_event(owner, lease_duration_seconds=30.0)
            results.append(claimed)

        t1 = threading.Thread(target=_worker, args=("dispatcher-A",))
        t2 = threading.Thread(target=_worker, args=("dispatcher-B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        winners = [r for r in results if r is not None]
        losers = [r for r in results if r is None]
        assert len(winners) == 1
        assert len(losers) == 1
        assert winners[0].status == OutboxEventStatus.IN_FLIGHT
        assert winners[0].lease_owner in ("dispatcher-A", "dispatcher-B")


def test_expired_lease_recovery(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=60)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-lease",
            event_type="TERMINATION",
            worker_id="worker-1",
            payload={"k": "v"},
            now_utc=t0,
        )
        claimed = outbox.claim_event("worker-dead", lease_duration_seconds=15.0, now_utc=t0)
        assert claimed is not None
        assert claimed.status == OutboxEventStatus.IN_FLIGHT

        # Not yet expired
        assert outbox.recover_expired_leases(now_utc=t0 + timedelta(seconds=10)) == []

        # Expired at t1: counted as abandoned attempt -> RETRY, lease invalidated
        recovered = outbox.recover_expired_leases(now_utc=t1)
        assert len(recovered) == 1
        assert recovered[0].event_id == "evt-lease"
        assert recovered[0].status == OutboxEventStatus.RETRY
        assert recovered[0].attempt_count == 1
        assert recovered[0].claim_count == 1
        assert recovered[0].lease_id is None
        assert recovered[0].lease_owner is None
        assert recovered[0].lease_expires_at_utc is None

        # Another worker can now claim it and receives a new lease_id
        reclaimed = outbox.claim_event("worker-alive", lease_duration_seconds=30.0, now_utc=t1)
        assert reclaimed is not None
        assert reclaimed.lease_owner == "worker-alive"
        assert reclaimed.lease_id is not None
        assert reclaimed.lease_id != claimed.lease_id
        assert reclaimed.claim_count == 2


def test_stale_fencing_token_rejected_on_claim_and_updates(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-fence",
            event_type="TERMINATION",
            worker_id="worker-1",
            fencing_token=10,
            payload={"k": "v"},
        )

        with pytest.raises(OutboxConflictError, match="Stale fencing_token"):
            outbox.claim_event("old-worker", event_id="evt-fence", expected_fencing_token=9)

        claimed = outbox.claim_event("current-worker", event_id="evt-fence", expected_fencing_token=10)
        assert claimed is not None

        with pytest.raises(OutboxConflictError, match="Stale fencing_token"):
            outbox.mark_delivered(
                "evt-fence",
                HubAckResponse(accepted=True, event_id="evt-fence"),
                lease_owner="current-worker",
                lease_id=claimed.lease_id,
                expected_fencing_token=9,
            )

        with pytest.raises(OutboxConflictError, match="Stale fencing_token"):
            outbox.mark_retry(
                "evt-fence",
                "err",
                lease_owner="current-worker",
                lease_id=claimed.lease_id,
                expected_fencing_token=9,
            )

        with pytest.raises(OutboxConflictError, match="Stale fencing_token"):
            outbox.mark_dead_letter(
                "evt-fence",
                "err",
                lease_owner="current-worker",
                lease_id=claimed.lease_id,
                expected_fencing_token=9,
            )


# ---------------------------------------------------------------------------
# Delivery, ACK, Retry, Backoff & Dead-Letter Tests
# ---------------------------------------------------------------------------


def test_valid_ack_and_repeated_ack_idempotency_and_no_revert_to_pending(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-ack-1",
            event_type="TERMINATION",
            worker_id="worker-1",
            payload={"ok": True},
        )
        claimed = outbox.claim_event("worker-1")
        assert claimed is not None

        delivered = outbox.mark_delivered(
            "evt-ack-1",
            HubAckResponse(accepted=True, event_id="evt-ack-1", ack_id="hub-ack-99"),
            lease_owner="worker-1",
            lease_id=claimed.lease_id,
        )
        assert delivered.status == OutboxEventStatus.DELIVERED
        assert delivered.delivered_at_utc is not None
        assert delivered.ack_id == "hub-ack-99"
        assert delivered.lease_id is None

        # Repeated ACK is idempotent
        repeated = outbox.mark_delivered(
            "evt-ack-1",
            HubAckResponse(accepted=True, event_id="evt-ack-1", ack_id="hub-ack-99"),
            lease_owner="worker-1",
            lease_id=claimed.lease_id,
        )
        assert repeated == delivered

        # DELIVERED cannot transition to RETRY or DEAD_LETTER
        with pytest.raises(OutboxStateTransitionError):
            outbox.mark_retry("evt-ack-1", "late error", lease_owner="worker-1", lease_id=claimed.lease_id)
        with pytest.raises(OutboxStateTransitionError):
            outbox.mark_dead_letter("evt-ack-1", "late fatal", lease_owner="worker-1", lease_id=claimed.lease_id)


def test_invalid_mismatched_and_ambiguous_ack_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-ack-target",
            event_type="TERMINATION",
            worker_id="worker-1",
            payload={"ok": True},
        )
        claimed = outbox.claim_event("worker-1")
        assert claimed is not None

        # Mismatched event_id
        with pytest.raises(OutboxAckError, match="mismatch"):
            outbox.mark_delivered(
                "evt-ack-target",
                HubAckResponse(accepted=True, event_id="evt-other"),
                lease_owner="worker-1",
                lease_id=claimed.lease_id,
            )

        # Ambiguous / unaccepted
        with pytest.raises(OutboxAckError, match="Ambiguous"):
            outbox.mark_delivered(
                "evt-ack-target",
                HubAckResponse(accepted=False, event_id="evt-ack-target"),
                lease_owner="worker-1",
                lease_id=claimed.lease_id,
            )

        # Missing ACK
        with pytest.raises(OutboxAckError):
            outbox.mark_delivered(
                "evt-ack-target",
                None,
                lease_owner="worker-1",
                lease_id=claimed.lease_id,
            )

        # Still IN_FLIGHT, never falsely marked DELIVERED
        current = outbox.get_event("evt-ack-target")
        assert current is not None
        assert current.status == OutboxEventStatus.IN_FLIGHT


def test_deliver_claimed_event_temporary_failures_and_backoff_jitter(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    t0 = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
    policy = RetryPolicy(max_attempts=4, base_delay_seconds=10.0, max_delay_seconds=60.0, jitter_ratio=0.2)

    with StateStore(db_path) as store:
        # Deterministic rng returning upper jitter bound (+20%)
        outbox = OutboxStore(store, retry_policy=policy, rng=lambda lo, hi: hi)
        outbox.enqueue_event(
            event_id="evt-temp",
            event_type="TERMINATION",
            worker_id="worker-1",
            payload={"k": "v"},
            now_utc=t0,
        )

        # 1) Timeout -> RETRY with delay 10 * 1.2 = 12s
        claimed1 = outbox.claim_event("w1", now_utc=t0)
        assert claimed1 is not None
        t_timeout = _MockTransport(lambda ev, key: (_ for _ in ()).throw(TimeoutError("hub timeout")))
        r1 = outbox.deliver_claimed_event(claimed1, t_timeout, now_utc=t0)
        assert r1.status == OutboxEventStatus.RETRY
        assert r1.attempt_count == 1
        assert "timeout" in (r1.last_error or "").lower()
        assert parse_utc_iso(r1.available_at_utc) == t0 + timedelta(seconds=12.0)

        # 2) Network failure -> RETRY with delay 20 * 1.2 = 24s
        t1 = t0 + timedelta(seconds=15.0)
        claimed2 = outbox.claim_event("w1", now_utc=t1)
        assert claimed2 is not None
        t_net = _MockTransport(lambda ev, key: (_ for _ in ()).throw(ConnectionError("reset by peer")))
        r2 = outbox.deliver_claimed_event(claimed2, t_net, now_utc=t1)
        assert r2.status == OutboxEventStatus.RETRY
        assert r2.attempt_count == 2
        assert parse_utc_iso(r2.available_at_utc) == t1 + timedelta(seconds=24.0)

        # 3) HTTP 503 temporary -> RETRY with delay 40 * 1.2 = 48s
        t2 = t1 + timedelta(seconds=30.0)
        claimed3 = outbox.claim_event("w1", now_utc=t2)
        assert claimed3 is not None
        t_503 = _MockTransport(
            lambda ev, key: HubAckResponse(accepted=False, event_id=ev.event_id, http_status=503, error_message="Service Unavailable")
        )
        r3 = outbox.deliver_claimed_event(claimed3, t_503, now_utc=t2)
        assert r3.status == OutboxEventStatus.RETRY
        assert r3.attempt_count == 3
        assert parse_utc_iso(r3.available_at_utc) == t2 + timedelta(seconds=48.0)

        # 4) HTTP 429 temporary -> hits max_attempts=4 -> DEAD_LETTER
        t3 = t2 + timedelta(seconds=50.0)
        claimed4 = outbox.claim_event("w1", now_utc=t3)
        assert claimed4 is not None
        t_429 = _MockTransport(
            lambda ev, key: HubAckResponse(accepted=False, event_id=ev.event_id, http_status=429, error_message="Rate Limited")
        )
        r4 = outbox.deliver_claimed_event(claimed4, t_429, now_utc=t3)
        assert r4.status == OutboxEventStatus.DEAD_LETTER
        assert r4.attempt_count == 4
        assert r4.delivered_at_utc is None
        assert r4.payload_json == '{"k":"v"}'


def test_permanent_http_error_moves_to_dead_letter_immediately(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-perm",
            event_type="TERMINATION",
            worker_id="worker-1",
            payload={"bad": "schema"},
        )
        claimed = outbox.claim_event("w1")
        assert claimed is not None

        t_400 = _MockTransport(
            lambda ev, key: HubAckResponse(
                accepted=False,
                event_id=ev.event_id,
                http_status=400,
                permanent_rejection=True,
                error_message="Bad Request",
            )
        )
        res = outbox.deliver_claimed_event(claimed, t_400)
        assert res.status == OutboxEventStatus.DEAD_LETTER
        assert res.attempt_count == 1
        assert res.delivered_at_utc is None
        assert res.payload_json == '{"bad":"schema"}'
        assert "Bad Request" in (res.last_error or "")


def test_transaction_rollback_leaves_no_partial_outbox_event(tmp_path: Path) -> None:
    db_path = tmp_path / "outbox.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        with pytest.raises(StateStorePersistenceError):
            with store._transaction() as conn:
                outbox.enqueue_event(
                    event_id="evt-rollback",
                    event_type="TERMINATION",
                    worker_id="worker-1",
                    payload={"x": 1},
                    conn=conn,
                )
                raise RuntimeError("Simulated crash before commit")

        assert outbox.get_event("evt-rollback") is None


# ---------------------------------------------------------------------------
# Corrective Round — Lease Identity, Ownership, Fencing, Recovery & Migration
# ---------------------------------------------------------------------------


def _raw_row(db_path: Path, event_id: str) -> sqlite3.Row:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM outbox_event WHERE event_id = ?;", (event_id,)).fetchone()
        assert row is not None
        return row
    finally:
        conn.close()


def test_claim_generates_unique_lease_ids_across_events_and_same_worker(tmp_path: Path) -> None:
    db_path = tmp_path / "lease_ids.db"
    t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=60)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(event_id="evt-1", event_type="T", worker_id="w1", payload={"i": 1}, now_utc=t0)
        outbox.enqueue_event(event_id="evt-2", event_type="T", worker_id="w1", payload={"i": 2}, now_utc=t0)

        c1 = outbox.claim_event("worker-A", event_id="evt-1", lease_duration_seconds=15.0, now_utc=t0)
        c2 = outbox.claim_event("worker-A", event_id="evt-2", lease_duration_seconds=15.0, now_utc=t0)
        assert c1 is not None and c2 is not None
        assert c1.lease_id is not None and c2.lease_id is not None
        assert c1.lease_id != c2.lease_id
        assert c1.claim_count == 1
        assert _raw_row(db_path, "evt-1")["lease_id"] == c1.lease_id

        # Expire and recover evt-1, then same worker-A claims it again -> different lease_id
        outbox.recover_expired_leases(now_utc=t1)
        assert _raw_row(db_path, "evt-1")["lease_id"] is None

        c1_again = outbox.claim_event("worker-A", event_id="evt-1", lease_duration_seconds=15.0, now_utc=t1)
        assert c1_again is not None
        assert c1_again.lease_id is not None
        assert c1_again.lease_id != c1.lease_id
        assert c1_again.claim_count == 2
        assert _raw_row(db_path, "evt-1")["lease_id"] == c1_again.lease_id


def test_none_or_wrong_lease_owner_and_lease_id_rejected_without_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "ownership.db"
    t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(event_id="evt-own", event_type="T", worker_id="w1", payload={"k": 1}, now_utc=t0)
        claimed = outbox.claim_event("worker-A", lease_duration_seconds=30.0, now_utc=t0)
        assert claimed is not None

        before = _raw_row(db_path, "evt-own")

        # Missing / None lease_owner or lease_id cannot bypass validation
        with pytest.raises(OutboxConflictError):
            outbox.mark_delivered(
                "evt-own",
                HubAckResponse(accepted=True, event_id="evt-own"),
                lease_owner=None,  # type: ignore[arg-type]
                lease_id=claimed.lease_id,
                now_utc=t0,
            )
        with pytest.raises(OutboxConflictError):
            outbox.mark_delivered(
                "evt-own",
                HubAckResponse(accepted=True, event_id="evt-own"),
                lease_owner="worker-A",
                lease_id=None,  # type: ignore[arg-type]
                now_utc=t0,
            )

        # Different worker cannot mutate
        with pytest.raises(OutboxConflictError, match="Lease owner mismatch"):
            outbox.mark_delivered(
                "evt-own",
                HubAckResponse(accepted=True, event_id="evt-own"),
                lease_owner="worker-B",
                lease_id=claimed.lease_id,
                now_utc=t0,
            )
        with pytest.raises(OutboxConflictError, match="Lease owner mismatch"):
            outbox.mark_retry("evt-own", "err", lease_owner="worker-B", lease_id=claimed.lease_id, now_utc=t0)
        with pytest.raises(OutboxConflictError, match="Lease owner mismatch"):
            outbox.mark_dead_letter("evt-own", "err", lease_owner="worker-B", lease_id=claimed.lease_id, now_utc=t0)

        # Wrong lease_id rejected
        with pytest.raises(OutboxConflictError, match="Lease ID mismatch"):
            outbox.mark_delivered(
                "evt-own",
                HubAckResponse(accepted=True, event_id="evt-own"),
                lease_owner="worker-A",
                lease_id="wrong-lease-id",
                now_utc=t0,
            )

        after = _raw_row(db_path, "evt-own")
        assert dict(after) == dict(before)


def test_expired_lease_cannot_confirm_retry_or_dead_letter(tmp_path: Path) -> None:
    db_path = tmp_path / "expired_mutations.db"
    t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)
    t_expired = t0 + timedelta(seconds=31)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(event_id="evt-exp", event_type="T", worker_id="w1", payload={"k": 1}, now_utc=t0)
        claimed = outbox.claim_event("worker-A", lease_duration_seconds=30.0, now_utc=t0)
        assert claimed is not None

        before = _raw_row(db_path, "evt-exp")

        with pytest.raises(OutboxConflictError, match="Lease expired"):
            outbox.mark_delivered(
                "evt-exp",
                HubAckResponse(accepted=True, event_id="evt-exp"),
                lease_owner="worker-A",
                lease_id=claimed.lease_id,
                now_utc=t_expired,
            )
        with pytest.raises(OutboxConflictError, match="Lease expired"):
            outbox.mark_retry(
                "evt-exp",
                "late timeout",
                lease_owner="worker-A",
                lease_id=claimed.lease_id,
                now_utc=t_expired,
            )
        with pytest.raises(OutboxConflictError, match="Lease expired"):
            outbox.mark_dead_letter(
                "evt-exp",
                "late fatal",
                lease_owner="worker-A",
                lease_id=claimed.lease_id,
                now_utc=t_expired,
            )

        after = _raw_row(db_path, "evt-exp")
        assert dict(after) == dict(before)


def test_late_ack_from_old_worker_and_same_worker_old_lease_id_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "late_ack.db"
    t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=45)
    t2 = t1 + timedelta(seconds=45)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-late",
            event_type="TERMINATION",
            worker_id="w1",
            fencing_token=10,
            payload={"x": 1},
            now_utc=t0,
        )

        # Scenario 1: Worker A claims -> expires -> recovered -> Worker B claims -> late ACK from A rejected
        claim_a = outbox.claim_event("worker-A", lease_duration_seconds=30.0, now_utc=t0)
        assert claim_a is not None
        outbox.recover_expired_leases(now_utc=t1)
        claim_b = outbox.claim_event("worker-B", lease_duration_seconds=30.0, now_utc=t1)
        assert claim_b is not None
        assert claim_b.lease_id != claim_a.lease_id

        before_b = _raw_row(db_path, "evt-late")
        with pytest.raises(OutboxConflictError):
            outbox.mark_delivered(
                "evt-late",
                HubAckResponse(accepted=True, event_id="evt-late", ack_id="ack-stale-A"),
                lease_owner="worker-A",
                lease_id=claim_a.lease_id,
                expected_fencing_token=10,
                now_utc=t1,
            )
        assert dict(_raw_row(db_path, "evt-late")) == dict(before_b)

        # Scenario 2: Expire B -> recover -> Worker A claims again with lease_id_2 -> late ACK with lease_id_1 rejected
        outbox.recover_expired_leases(now_utc=t2)
        claim_a2 = outbox.claim_event("worker-A", lease_duration_seconds=30.0, now_utc=t2)
        assert claim_a2 is not None
        assert claim_a2.lease_id != claim_a.lease_id

        before_a2 = _raw_row(db_path, "evt-late")
        with pytest.raises(OutboxConflictError, match="Lease ID mismatch"):
            outbox.mark_delivered(
                "evt-late",
                HubAckResponse(accepted=True, event_id="evt-late", ack_id="ack-stale-A1"),
                lease_owner="worker-A",
                lease_id=claim_a.lease_id,
                expected_fencing_token=10,
                now_utc=t2,
            )
        assert dict(_raw_row(db_path, "evt-late")) == dict(before_a2)

        # Valid lease_id_2 succeeds
        delivered = outbox.mark_delivered(
            "evt-late",
            HubAckResponse(accepted=True, event_id="evt-late", ack_id="ack-valid-A2"),
            lease_owner="worker-A",
            lease_id=claim_a2.lease_id,
            expected_fencing_token=10,
            now_utc=t2,
        )
        assert delivered.status == OutboxEventStatus.DELIVERED
        assert delivered.ack_id == "ack-valid-A2"
        assert _raw_row(db_path, "evt-late")["status"] == "DELIVERED"


def test_fencing_token_strict_rules_for_fenced_and_unfenced_events(tmp_path: Path) -> None:
    db_path = tmp_path / "fencing_strict.db"
    t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-fenced",
            event_type="T",
            worker_id="w1",
            fencing_token=10,
            payload={"k": 1},
            now_utc=t0,
        )
        outbox.enqueue_event(
            event_id="evt-unfenced",
            event_type="T",
            worker_id="w1",
            fencing_token=None,
            payload={"k": 2},
            now_utc=t0,
        )

        cf = outbox.claim_event("w1", event_id="evt-fenced", expected_fencing_token=10, now_utc=t0)
        cu = outbox.claim_event("w1", event_id="evt-unfenced", now_utc=t0)
        assert cf is not None and cu is not None

        # Fenced event rejects missing token, stale token, and higher divergent token
        with pytest.raises(OutboxConflictError, match="Missing expected_fencing_token"):
            outbox.mark_delivered(
                "evt-fenced",
                HubAckResponse(accepted=True, event_id="evt-fenced"),
                lease_owner="w1",
                lease_id=cf.lease_id,
                expected_fencing_token=None,
                now_utc=t0,
            )
        with pytest.raises(OutboxConflictError, match="Fencing token mismatch"):
            outbox.mark_delivered(
                "evt-fenced",
                HubAckResponse(accepted=True, event_id="evt-fenced"),
                lease_owner="w1",
                lease_id=cf.lease_id,
                expected_fencing_token=11,
                now_utc=t0,
            )
        # Valid fencing token succeeds
        ok_f = outbox.mark_delivered(
            "evt-fenced",
            HubAckResponse(accepted=True, event_id="evt-fenced"),
            lease_owner="w1",
            lease_id=cf.lease_id,
            expected_fencing_token=10,
            now_utc=t0,
        )
        assert ok_f.status == OutboxEventStatus.DELIVERED

        # Unfenced event rejects invented fencing token, accepts None with valid lease
        with pytest.raises(OutboxConflictError, match="Unexpected fencing_token"):
            outbox.mark_delivered(
                "evt-unfenced",
                HubAckResponse(accepted=True, event_id="evt-unfenced"),
                lease_owner="w1",
                lease_id=cu.lease_id,
                expected_fencing_token=1,
                now_utc=t0,
            )
        ok_u = outbox.mark_delivered(
            "evt-unfenced",
            HubAckResponse(accepted=True, event_id="evt-unfenced"),
            lease_owner="w1",
            lease_id=cu.lease_id,
            expected_fencing_token=None,
            now_utc=t0,
        )
        assert ok_u.status == OutboxEventStatus.DELIVERED
        assert ok_u.fencing_token is None


def test_repeated_abandoned_claims_respect_max_attempts_and_dead_letter_not_resent(tmp_path: Path) -> None:
    db_path = tmp_path / "abandoned_cap.db"
    t = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)
    policy = RetryPolicy(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=10.0, jitter_ratio=0.0)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store, retry_policy=policy)
        outbox.enqueue_event(
            event_id="evt-crash-loop",
            event_type="TERMINATION",
            worker_id="w1",
            fencing_token=5,
            payload={"important": "data"},
            now_utc=t,
        )

        # 3 consecutive claims that crash before ACK -> hits max_attempts=3 -> DEAD_LETTER
        for attempt in range(1, 4):
            claimed = outbox.claim_event(f"worker-{attempt}", lease_duration_seconds=10.0, now_utc=t)
            assert claimed is not None
            assert claimed.claim_count == attempt
            t = t + timedelta(seconds=15)
            recovered = outbox.recover_expired_leases(now_utc=t)
            assert len(recovered) == 1
            assert recovered[0].attempt_count == attempt
            assert recovered[0].lease_id is None
            if attempt < 3:
                assert recovered[0].status == OutboxEventStatus.RETRY
            else:
                assert recovered[0].status == OutboxEventStatus.DEAD_LETTER
                assert recovered[0].payload_json == '{"important":"data"}'
                assert recovered[0].delivered_at_utc is None

        # Persisted state is DEAD_LETTER and never returned for automatic delivery
        raw = _raw_row(db_path, "evt-crash-loop")
        assert raw["status"] == "DEAD_LETTER"
        assert raw["attempt_count"] == 3
        assert raw["claim_count"] == 3
        assert outbox.get_pending_events(now_utc=t) == []
        assert outbox.claim_event("worker-4", now_utc=t) is None


def test_checksum_includes_lease_id_and_corrupted_inflight_not_auto_recovered(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt_lease.db"
    t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)
    t_exp = t0 + timedelta(seconds=60)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(event_id="evt-corrupt-lease", event_type="T", worker_id="w1", payload={"a": 1}, now_utc=t0)
        outbox.enqueue_event(event_id="evt-valid-lease", event_type="T", worker_id="w1", payload={"a": 2}, now_utc=t0)
        c1 = outbox.claim_event("w1", event_id="evt-corrupt-lease", lease_duration_seconds=10.0, now_utc=t0)
        c2 = outbox.claim_event("w1", event_id="evt-valid-lease", lease_duration_seconds=10.0, now_utc=t0)
        assert c1 is not None and c2 is not None

    # Manually tamper lease_id in SQLite without updating checksum
    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE outbox_event SET lease_id = 'tampered-uuid' WHERE event_id = 'evt-corrupt-lease';")
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        with pytest.raises(OutboxCorruptionError, match="INCONCLUSIVE"):
            outbox.get_event("evt-corrupt-lease")

        # recover_expired_leases recovers valid row, skips corrupted row and preserves evidence
        recovered = outbox.recover_expired_leases(now_utc=t_exp)
        assert [r.event_id for r in recovered] == ["evt-valid-lease"]
        assert _raw_row(db_path, "evt-corrupt-lease")["lease_id"] == "tampered-uuid"
        assert _raw_row(db_path, "evt-corrupt-lease")["status"] == "IN_FLIGHT"

        reports = {r.record_key: r for r in outbox.verify_integrity()}
        assert reports["evt-corrupt-lease"].status == IntegrityStatus.INCONCLUSIVE
        assert reports["evt-valid-lease"].status == IntegrityStatus.VALID


def test_migration_v1_to_v2_preserves_events_is_idempotent_and_rolls_back_on_error(tmp_path: Path) -> None:
    from desktop_worker.watchdog.state_store import compute_canonical_checksum

    db_path = tmp_path / "legacy_v1.db"
    # Create a Task 3 StateStore and manually seed an Outbox v1 schema + historical rows
    with StateStore(db_path) as store:
        with store._transaction() as conn:
            conn.execute(
                """
                CREATE TABLE outbox_event (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    execution_id TEXT,
                    attempt_id TEXT,
                    job_id TEXT,
                    worker_id TEXT NOT NULL,
                    fencing_token INTEGER,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    available_at_utc TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at_utc TEXT,
                    delivered_at_utc TEXT,
                    last_error TEXT,
                    status TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at_utc TEXT,
                    ack_id TEXT,
                    schema_version INTEGER NOT NULL,
                    checksum_sha256 TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT INTO schema_metadata (key, value) VALUES ('outbox_schema_version', '1');"
            )
            v1_row = {
                "schema_version": 1,
                "event_id": "evt-v1-delivered",
                "event_type": "TERMINATION",
                "execution_id": "exec-1",
                "attempt_id": "att-1",
                "job_id": "job-1",
                "worker_id": "w1",
                "fencing_token": 7,
                "payload_json": '{"legacy":true}',
                "created_at_utc": "2026-11-19T00:00:00+00:00",
                "available_at_utc": "2026-11-19T00:00:00+00:00",
                "attempt_count": 1,
                "last_attempt_at_utc": "2026-11-19T00:00:05+00:00",
                "delivered_at_utc": "2026-11-19T00:00:06+00:00",
                "last_error": None,
                "status": "DELIVERED",
                "lease_owner": None,
                "lease_expires_at_utc": None,
                "ack_id": "ack-legacy-1",
            }
            v1_checksum = compute_canonical_checksum(v1_row)
            conn.execute(
                """
                INSERT INTO outbox_event (
                    event_id, event_type, execution_id, attempt_id, job_id,
                    worker_id, fencing_token, payload_json, created_at_utc,
                    available_at_utc, attempt_count, last_attempt_at_utc,
                    delivered_at_utc, last_error, status, lease_owner,
                    lease_expires_at_utc, ack_id, schema_version, checksum_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    v1_row["event_id"], v1_row["event_type"], v1_row["execution_id"],
                    v1_row["attempt_id"], v1_row["job_id"], v1_row["worker_id"],
                    v1_row["fencing_token"], v1_row["payload_json"], v1_row["created_at_utc"],
                    v1_row["available_at_utc"], v1_row["attempt_count"], v1_row["last_attempt_at_utc"],
                    v1_row["delivered_at_utc"], v1_row["last_error"], v1_row["status"],
                    v1_row["lease_owner"], v1_row["lease_expires_at_utc"], v1_row["ack_id"],
                    v1_row["schema_version"], v1_checksum,
                ),
            )

    # Opening OutboxStore migrates v1 -> v2 transactionally
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        ev = outbox.get_event("evt-v1-delivered")
        assert ev is not None
        assert ev.schema_version == OUTBOX_SCHEMA_VERSION
        assert ev.payload_json == '{"legacy":true}'
        assert ev.ack_id == "ack-legacy-1"
        assert ev.lease_id is None
        assert ev.claim_count == 1
        assert all(r.status == IntegrityStatus.VALID for r in outbox.verify_integrity())

    # Re-opening OutboxStore is idempotent (no duplicate columns or errors)
    with StateStore(db_path) as store:
        outbox2 = OutboxStore(store)
        assert outbox2.get_event("evt-v1-delivered") is not None

    # Test rollback on migration failure
    db_fail = tmp_path / "legacy_v1_fail.db"
    with StateStore(db_fail) as store:
        with store._transaction() as conn:
            conn.execute(
                "INSERT INTO schema_metadata (key, value) VALUES ('outbox_schema_version', '1');"
            )
            # Deliberately omit outbox_event table so ALTER TABLE fails mid-migration

    with StateStore(db_fail) as store:
        with pytest.raises(OutboxSchemaError):
            OutboxStore(store)
        # Verify schema_metadata was rolled back and still at '1'
        ver = store._require_conn().execute(
            "SELECT value FROM schema_metadata WHERE key = 'outbox_schema_version';"
        ).fetchone()
        assert ver is not None and ver["value"] == "1"


# ---------------------------------------------------------------------------
# Final Corrective Round — Schema Consistency & Corruption Isolation Tests
# ---------------------------------------------------------------------------


def _raw_all_rows(db_path: Path) -> List[Dict[str, object]]:
    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in raw.execute("SELECT * FROM outbox_event ORDER BY event_id;").fetchall()]
    finally:
        raw.close()


def test_schema_new_db_creates_v2_columns_indexes_and_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "new_v2.db"
    with StateStore(db_path) as store:
        OutboxStore(store)
        conn = store._require_conn()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(outbox_event);").fetchall()}
        assert {"event_id", "claim_count", "lease_id", "checksum_sha256", "schema_version"}.issubset(cols)
        idxs = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'outbox_event';"
            ).fetchall()
        }
        assert {
            "idx_outbox_status_available",
            "idx_outbox_inflight_lease",
            "idx_outbox_exec_attempt",
            "idx_outbox_delivered",
        }.issubset(idxs)
        ver = conn.execute(
            "SELECT value FROM schema_metadata WHERE key = 'outbox_schema_version';"
        ).fetchone()
        assert ver is not None and ver["value"] == str(OUTBOX_SCHEMA_VERSION)


def test_schema_reopen_valid_v2_is_idempotent_no_duplicate_columns_or_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "reopen_v2.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(event_id="evt-1", event_type="T", worker_id="w1", payload={"k": 1})
        conn = store._require_conn()
        cols_first = [r["name"] for r in conn.execute("PRAGMA table_info(outbox_event);").fetchall()]
        idxs_first = sorted(
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'outbox_event';"
            ).fetchall()
        )

    with StateStore(db_path) as store:
        outbox2 = OutboxStore(store)
        assert outbox2.get_event("evt-1") is not None
        conn = store._require_conn()
        cols_second = [r["name"] for r in conn.execute("PRAGMA table_info(outbox_event);").fetchall()]
        idxs_second = sorted(
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'outbox_event';"
            ).fetchall()
        )
        assert cols_second == cols_first
        assert len(cols_second) == len(set(cols_second))
        assert idxs_second == idxs_first


def test_schema_existing_table_without_metadata_rejected_and_preserves_data(tmp_path: Path) -> None:
    db_path = tmp_path / "table_no_meta.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(event_id="evt-keep", event_type="T", worker_id="w1", payload={"preserve": True})

    before_rows = _raw_all_rows(db_path)

    # Remove outbox_schema_version metadata while leaving outbox_event intact
    raw = sqlite3.connect(str(db_path))
    raw.execute("DELETE FROM schema_metadata WHERE key = 'outbox_schema_version';")
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        with pytest.raises(OutboxSchemaError, match="without 'outbox_schema_version'"):
            OutboxStore(store)
        # Metadata must NOT have been silently registered
        meta = store._require_conn().execute(
            "SELECT value FROM schema_metadata WHERE key = 'outbox_schema_version';"
        ).fetchone()
        assert meta is None

    assert _raw_all_rows(db_path) == before_rows


def test_schema_v2_metadata_with_missing_column_rejected_and_preserves_data(tmp_path: Path) -> None:
    db_path = tmp_path / "v2_missing_col.db"
    with StateStore(db_path) as store:
        with store._transaction() as conn:
            # Create a table missing lease_id and claim_count, but claim version 2
            conn.execute(
                """
                CREATE TABLE outbox_event (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    execution_id TEXT,
                    attempt_id TEXT,
                    job_id TEXT,
                    worker_id TEXT NOT NULL,
                    fencing_token INTEGER,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    available_at_utc TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at_utc TEXT,
                    delivered_at_utc TEXT,
                    last_error TEXT,
                    status TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at_utc TEXT,
                    ack_id TEXT,
                    schema_version INTEGER NOT NULL,
                    checksum_sha256 TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT INTO schema_metadata (key, value) VALUES ('outbox_schema_version', '2');"
            )
            conn.execute(
                """
                INSERT INTO outbox_event (
                    event_id, event_type, worker_id, payload_json, created_at_utc,
                    available_at_utc, attempt_count, status, schema_version, checksum_sha256
                ) VALUES ('evt-raw', 'T', 'w1', '{"x":1}', '2026-11-19T00:00:00+00:00',
                          '2026-11-19T00:00:00+00:00', 0, 'PENDING', 2, 'abc');
                """
            )

    before_rows = _raw_all_rows(db_path)
    with StateStore(db_path) as store:
        with pytest.raises(OutboxSchemaError, match="missing required columns"):
            OutboxStore(store)
    assert _raw_all_rows(db_path) == before_rows


def test_schema_v2_metadata_with_missing_index_rejected_and_preserves_data(tmp_path: Path) -> None:
    db_path = tmp_path / "v2_missing_idx.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(event_id="evt-idx", event_type="T", worker_id="w1", payload={"ok": True})

    before_rows = _raw_all_rows(db_path)

    raw = sqlite3.connect(str(db_path))
    raw.execute("DROP INDEX idx_outbox_delivered;")
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        with pytest.raises(OutboxSchemaError, match="missing required indexes"):
            OutboxStore(store)
    assert _raw_all_rows(db_path) == before_rows


def test_schema_v1_migration_failure_rolls_back_completely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from desktop_worker.watchdog.state_store import compute_canonical_checksum

    db_path = tmp_path / "v1_mid_fail.db"
    with StateStore(db_path) as store:
        with store._transaction() as conn:
            conn.execute(
                """
                CREATE TABLE outbox_event (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    execution_id TEXT,
                    attempt_id TEXT,
                    job_id TEXT,
                    worker_id TEXT NOT NULL,
                    fencing_token INTEGER,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    available_at_utc TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at_utc TEXT,
                    delivered_at_utc TEXT,
                    last_error TEXT,
                    status TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at_utc TEXT,
                    ack_id TEXT,
                    schema_version INTEGER NOT NULL,
                    checksum_sha256 TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT INTO schema_metadata (key, value) VALUES ('outbox_schema_version', '1');"
            )
            v1_row = {
                "schema_version": 1,
                "event_id": "evt-v1-rb",
                "event_type": "T",
                "execution_id": None,
                "attempt_id": None,
                "job_id": None,
                "worker_id": "w1",
                "fencing_token": None,
                "payload_json": '{"rb":true}',
                "created_at_utc": "2026-11-19T00:00:00+00:00",
                "available_at_utc": "2026-11-19T00:00:00+00:00",
                "attempt_count": 0,
                "last_attempt_at_utc": None,
                "delivered_at_utc": None,
                "last_error": None,
                "status": "PENDING",
                "lease_owner": None,
                "lease_expires_at_utc": None,
                "ack_id": None,
            }
            conn.execute(
                """
                INSERT INTO outbox_event (
                    event_id, event_type, worker_id, payload_json, created_at_utc,
                    available_at_utc, attempt_count, status, schema_version, checksum_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    v1_row["event_id"], v1_row["event_type"], v1_row["worker_id"],
                    v1_row["payload_json"], v1_row["created_at_utc"], v1_row["available_at_utc"],
                    v1_row["attempt_count"], v1_row["status"], v1_row["schema_version"],
                    compute_canonical_checksum(v1_row),
                ),
            )

    before_rows = _raw_all_rows(db_path)

    # Inject a failure right after _migrate_v1_to_v2 runs, inside the same transaction
    def _boom(self, conn):
        raise OutboxSchemaError("Simulated post-migration validation failure")

    monkeypatch.setattr(OutboxStore, "_validate_v2_schema", _boom)

    with StateStore(db_path) as store:
        with pytest.raises(OutboxSchemaError, match="Simulated post-migration validation failure"):
            OutboxStore(store)
        conn = store._require_conn()
        ver = conn.execute(
            "SELECT value FROM schema_metadata WHERE key = 'outbox_schema_version';"
        ).fetchone()
        assert ver is not None and ver["value"] == "1"
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(outbox_event);").fetchall()}
        assert "lease_id" not in cols
        assert "claim_count" not in cols

    assert _raw_all_rows(db_path) == before_rows


def test_corrupted_first_event_does_not_block_intact_second_event_and_is_preserved_byte_for_byte(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "corrupt_isolation.db"
    t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=1)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-1-corrupt",
            event_type="HEARTBEAT",
            worker_id="w1",
            payload={"seq": 1},
            now_utc=t0,
        )
        outbox.enqueue_event(
            event_id="evt-2-intact",
            event_type="HEARTBEAT",
            worker_id="w1",
            payload={"seq": 2},
            now_utc=t1,
        )

    # Corrupt the first event's payload_json in SQLite without updating checksum
    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE outbox_event SET payload_json = '{\"seq\":999}' WHERE event_id = 'evt-1-corrupt';")
    raw.commit()
    raw.close()

    corrupt_before = _raw_row(db_path, "evt-1-corrupt")

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)

        # Explicit claim of corrupted event returns None without mutating it
        assert outbox.claim_event("worker-a", event_id="evt-1-corrupt", now_utc=t1) is None

        # General claim skips corrupted first event and claims the intact second event
        claimed = outbox.claim_event("worker-a", now_utc=t1)
        assert claimed is not None
        assert claimed.event_id == "evt-2-intact"
        assert claimed.status == OutboxEventStatus.IN_FLIGHT

        # Deliver the intact second event normally
        transport = _MockTransport(
            lambda ev, key: HubAckResponse(accepted=True, event_id=ev.event_id, ack_id="ack-2", http_status=200)
        )
        delivered = outbox.deliver_claimed_event(claimed, transport, now_utc=t1)
        assert delivered.status == OutboxEventStatus.DELIVERED
        assert delivered.ack_id == "ack-2"

        # Corrupted event remains byte-for-byte identical, checksum NOT recalculated, NOT delivered
        corrupt_after = _raw_row(db_path, "evt-1-corrupt")
        assert corrupt_after == corrupt_before
        assert corrupt_after["checksum_sha256"] == corrupt_before["checksum_sha256"]
        assert corrupt_after["status"] == "PENDING"

        # verify_integrity still classifies corrupted event as INCONCLUSIVE
        reports = {r.record_key: r for r in outbox.verify_integrity()}
        assert reports["evt-1-corrupt"].status == IntegrityStatus.INCONCLUSIVE
        assert reports["evt-2-intact"].status == IntegrityStatus.VALID


def test_all_eligible_events_corrupted_returns_none_and_delivers_nothing(tmp_path: Path) -> None:
    db_path = tmp_path / "all_corrupt.db"
    t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(event_id="evt-c1", event_type="T", worker_id="w1", payload={"n": 1}, now_utc=t0)
        outbox.enqueue_event(event_id="evt-c2", event_type="T", worker_id="w1", payload={"n": 2}, now_utc=t0)

    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE outbox_event SET payload_json = '{\"tampered\":true}';")
    raw.commit()
    raw.close()

    before = _raw_all_rows(db_path)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        assert outbox.claim_event("worker-a", now_utc=t0) is None
        reports = {r.record_key: r for r in outbox.verify_integrity()}
        assert reports["evt-c1"].status == IntegrityStatus.INCONCLUSIVE
        assert reports["evt-c2"].status == IntegrityStatus.INCONCLUSIVE

    assert _raw_all_rows(db_path) == before


def test_concurrent_workers_skip_corrupted_and_claim_intact_event_only_once(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent_corrupt.db"
    t0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=1)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(event_id="evt-0-bad", event_type="T", worker_id="w1", payload={"v": 0}, now_utc=t0)
        outbox.enqueue_event(event_id="evt-1-good", event_type="T", worker_id="w1", payload={"v": 1}, now_utc=t1)

    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE outbox_event SET payload_json = '{\"v\":999}' WHERE event_id = 'evt-0-bad';")
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        barrier = threading.Barrier(4)
        claimed_results: List[Optional[OutboxEventRecord]] = [None] * 4

        def _worker(idx: int) -> None:
            barrier.wait()
            claimed_results[idx] = outbox.claim_event(f"worker-{idx}", now_utc=t1)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        winners = [r for r in claimed_results if r is not None]
        assert len(winners) == 1
        assert winners[0].event_id == "evt-1-good"
        assert winners[0].claim_count == 1


# ---------------------------------------------------------------------------
# Final Corrective Round — Structural Index Validation Tests (A-H)
# ---------------------------------------------------------------------------


def _raw_schema_snapshot(db_path: Path) -> Dict[str, object]:
    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in raw.execute("SELECT * FROM outbox_event ORDER BY event_id;").fetchall()]
        meta = [dict(r) for r in raw.execute("SELECT * FROM schema_metadata ORDER BY key;").fetchall()]
        indexes = [
            dict(r)
            for r in raw.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'index' ORDER BY name;"
            ).fetchall()
        ]
        checksums = {r["event_id"]: r["checksum_sha256"] for r in rows}
        return {"rows": rows, "meta": meta, "indexes": indexes, "checksums": checksums}
    finally:
        raw.close()


def test_index_validation_a_valid_v2_indexes_succeed(tmp_path: Path) -> None:
    db_path = tmp_path / "idx_valid.db"
    with StateStore(db_path) as store:
        OutboxStore(store)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        assert outbox.get_pending_events() == []


def test_index_validation_b_same_name_wrong_column_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "idx_wrong_col.db"
    with StateStore(db_path) as store:
        OutboxStore(store)

    raw = sqlite3.connect(str(db_path))
    raw.execute("DROP INDEX idx_outbox_status_available;")
    raw.execute("CREATE INDEX idx_outbox_status_available ON outbox_event(worker_id);")
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        with pytest.raises(OutboxSchemaError, match="expected columns"):
            OutboxStore(store)


def test_index_validation_c_wrong_column_order_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "idx_wrong_order.db"
    with StateStore(db_path) as store:
        OutboxStore(store)

    raw = sqlite3.connect(str(db_path))
    raw.execute("DROP INDEX idx_outbox_status_available;")
    raw.execute(
        "CREATE INDEX idx_outbox_status_available ON outbox_event(available_at_utc, status);"
    )
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        with pytest.raises(OutboxSchemaError, match="expected columns"):
            OutboxStore(store)


def test_index_validation_d_wrong_uniqueness_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "idx_wrong_unique.db"
    with StateStore(db_path) as store:
        OutboxStore(store)

    raw = sqlite3.connect(str(db_path))
    raw.execute("DROP INDEX idx_outbox_exec_attempt;")
    raw.execute(
        "CREATE UNIQUE INDEX idx_outbox_exec_attempt ON outbox_event(execution_id, attempt_id);"
    )
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        with pytest.raises(OutboxSchemaError, match="expected unique=False"):
            OutboxStore(store)


def test_index_validation_e_wrong_table_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "idx_wrong_table.db"
    with StateStore(db_path) as store:
        OutboxStore(store)

    raw = sqlite3.connect(str(db_path))
    raw.execute("DROP INDEX idx_outbox_delivered;")
    raw.execute("CREATE TABLE other_tbl (status TEXT, delivered_at_utc TEXT);")
    raw.execute("CREATE INDEX idx_outbox_delivered ON other_tbl(status, delivered_at_utc);")
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        with pytest.raises(OutboxSchemaError, match="missing required indexes"):
            OutboxStore(store)


def test_index_validation_f_unexpected_partial_where_clause_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "idx_partial.db"
    with StateStore(db_path) as store:
        OutboxStore(store)

    raw = sqlite3.connect(str(db_path))
    raw.execute("DROP INDEX idx_outbox_delivered;")
    raw.execute(
        "CREATE INDEX idx_outbox_delivered ON outbox_event(status, delivered_at_utc) "
        "WHERE status = 'DELIVERED';"
    )
    raw.commit()
    raw.close()

    with StateStore(db_path) as store:
        with pytest.raises(OutboxSchemaError, match="expected partial=False"):
            OutboxStore(store)


def test_index_validation_g_failure_preserves_db_rows_metadata_indexes_and_checksums(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "idx_preserve.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-preserve-1",
            event_type="HEARTBEAT",
            worker_id="w1",
            payload={"keep": True},
        )

    raw = sqlite3.connect(str(db_path))
    raw.execute("DROP INDEX idx_outbox_inflight_lease;")
    raw.execute("CREATE INDEX idx_outbox_inflight_lease ON outbox_event(worker_id);")
    raw.commit()
    raw.close()

    before = _raw_schema_snapshot(db_path)

    with StateStore(db_path) as store:
        with pytest.raises(OutboxSchemaError):
            OutboxStore(store)

    after = _raw_schema_snapshot(db_path)
    assert after == before


def test_index_validation_h_repeated_reopen_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "idx_idempotent.db"
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(event_id="evt-idem", event_type="T", worker_id="w1", payload={"v": 1})

    first_snapshot = _raw_schema_snapshot(db_path)
    for _ in range(3):
        with StateStore(db_path) as store:
            outbox = OutboxStore(store)
            assert outbox.get_event("evt-idem") is not None

    assert _raw_schema_snapshot(db_path) == first_snapshot


# ---------------------------------------------------------------------------
# New DB Schema Validation & Rollback Tests (Scenario A)
# ---------------------------------------------------------------------------


def test_new_db_index_name_collision_rolls_back_completely(tmp_path: Path) -> None:
    """Scenario A: index name collision on another table must fail and roll back completely."""
    db_path = tmp_path / "new_db_collision.db"

    with StateStore(db_path) as store:
        with store._transaction() as conn:
            conn.execute("CREATE TABLE other_tbl (id TEXT PRIMARY KEY, val TEXT);")
            conn.execute(
                "CREATE INDEX idx_outbox_status_available ON other_tbl(val);"
            )
            conn.execute("INSERT INTO other_tbl (id, val) VALUES ('row-1', 'keep');")

        with pytest.raises(OutboxSchemaError, match="missing required indexes"):
            OutboxStore(store)

    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    try:
        # outbox_schema_version must NOT be registered
        meta_row = raw.execute(
            "SELECT value FROM schema_metadata WHERE key = 'outbox_schema_version';"
        ).fetchone()
        assert meta_row is None

        # outbox_event table must have been rolled back
        outbox_tbl = raw.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'outbox_event';"
        ).fetchone()
        assert outbox_tbl is None

        # None of the other outbox indexes may remain
        outbox_idxs = raw.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' AND name IN "
            "('idx_outbox_inflight_lease', 'idx_outbox_exec_attempt', 'idx_outbox_delivered');"
        ).fetchall()
        assert outbox_idxs == []

        # other_tbl, its data, and its original colliding index must remain intact
        other_tbl = raw.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'other_tbl';"
        ).fetchone()
        assert other_tbl is not None

        other_row = raw.execute("SELECT id, val FROM other_tbl WHERE id = 'row-1';").fetchone()
        assert other_row is not None
        assert other_row["val"] == "keep"

        colliding_idx = raw.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' AND name = 'idx_outbox_status_available';"
        ).fetchone()
        assert colliding_idx is not None
        assert colliding_idx["tbl_name"] == "other_tbl"
    finally:
        raw.close()


def test_new_db_without_collision_creates_validates_and_registers_v2(tmp_path: Path) -> None:
    """Scenario A: clean new DB creates table + all indexes, validates v2, and records version 2."""
    db_path = tmp_path / "new_db_clean.db"

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        with store._transaction() as conn:
            # Explicit structural validation passes without error
            outbox._validate_v2_schema(conn)

            meta_row = conn.execute(
                "SELECT value FROM schema_metadata WHERE key = 'outbox_schema_version';"
            ).fetchone()
            assert meta_row is not None
            assert int(meta_row["value"]) == 2

            idx_names = {
                r["name"]
                for r in conn.execute("PRAGMA index_list('outbox_event');").fetchall()
            }
            assert {
                "idx_outbox_status_available",
                "idx_outbox_inflight_lease",
                "idx_outbox_exec_attempt",
                "idx_outbox_delivered",
            }.issubset(idx_names)


def test_reopen_valid_v2_db_is_idempotent_and_preserves_schema(tmp_path: Path) -> None:
    """Scenario C: reopening a valid v2 DB succeeds idempotently without mutating schema."""
    db_path = tmp_path / "reopen_valid_v2.db"

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-reopen-v2",
            event_type="HEARTBEAT",
            worker_id="w1",
            payload={"ok": True},
        )

    before_snapshot = _raw_schema_snapshot(db_path)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        rec = outbox.get_event("evt-reopen-v2")
        assert rec is not None
        assert rec.schema_version == 2

    after_snapshot = _raw_schema_snapshot(db_path)
    assert after_snapshot == before_snapshot
