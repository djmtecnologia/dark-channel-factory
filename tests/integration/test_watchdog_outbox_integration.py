"""Integration & resilience tests for Watchdog Durable Outbox (Sprint 2.7 - Task 4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Set

from desktop_worker.watchdog.identity import CanonicalExecutionIdentity
from desktop_worker.watchdog.outbox import (
    HubAckResponse,
    OutboxEventRecord,
    OutboxEventStatus,
    OutboxStore,
    RetryPolicy,
)
from desktop_worker.watchdog.state_store import (
    ExecutionStatus,
    IntegrityStatus,
    StateStore,
)


class _IdempotentHubMock:
    """Simulated Cloud Hub supporting idempotency by event_id."""

    def __init__(self, *, drop_first_ack: bool = False) -> None:
        self.received_events: Dict[str, OutboxEventRecord] = {}
        self.seen_keys: Set[str] = set()
        self.call_count = 0
        self._drop_first_ack = drop_first_ack

    def send_event(self, event: OutboxEventRecord, *, idempotency_key: str) -> HubAckResponse:
        self.call_count += 1
        assert idempotency_key == event.event_id
        # Hub persists/deduplicates by idempotency_key
        if idempotency_key not in self.seen_keys:
            self.seen_keys.add(idempotency_key)
            self.received_events[idempotency_key] = event

        if self._drop_first_ack and self.call_count == 1:
            # Simulate crash/network loss AFTER remote receipt and BEFORE local ACK
            raise ConnectionError("Connection dropped after Hub processed event")

        return HubAckResponse(
            accepted=True,
            event_id=event.event_id,
            ack_id=f"ack-{event.event_id}",
            http_status=200,
        )


def test_resilience_crash_after_persist_before_send_and_restart(tmp_path: Path) -> None:
    """Worker crashes after enqueueing PENDING event, before sending."""
    db_path = tmp_path / "resilience_pending.db"
    t0 = datetime(2026, 11, 19, 14, 0, 0, tzinfo=timezone.utc)

    # Session 1: persist event and exit immediately (crash before send)
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-crash-1",
            event_type="TERMINATION_CONFIRMED",
            worker_id="worker-1",
            execution_id="exec-1",
            attempt_id="att-1",
            job_id="job-1",
            fencing_token=7,
            payload={"verified": True},
            now_utc=t0,
        )

    # Session 2: restart, recover PENDING, claim and deliver
    hub = _IdempotentHubMock()
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        pending = outbox.get_pending_events(now_utc=t0)
        assert len(pending) == 1
        claimed = outbox.claim_event("worker-1-restarted", now_utc=t0)
        assert claimed is not None
        delivered = outbox.deliver_claimed_event(claimed, hub, now_utc=t0)
        assert delivered.status == OutboxEventStatus.DELIVERED
        assert hub.call_count == 1
        assert all(r.status == IntegrityStatus.VALID for r in outbox.verify_integrity())


def test_resilience_crash_during_inflight_and_lease_recovery(tmp_path: Path) -> None:
    """Worker crashes while event is IN_FLIGHT; after restart & lease expiry it is delivered."""
    db_path = tmp_path / "resilience_inflight.db"
    t0 = datetime(2026, 11, 19, 15, 0, 0, tzinfo=timezone.utc)
    t_after_expiry = t0 + timedelta(seconds=45)

    # Session 1: enqueue & claim (IN_FLIGHT), then crash without updating status
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-inflight-crash",
            event_type="TERMINATION_CONFIRMED",
            worker_id="worker-1",
            fencing_token=12,
            payload={"step": "inflight"},
            now_utc=t0,
        )
        claimed = outbox.claim_event("worker-crashed", lease_duration_seconds=30.0, now_utc=t0)
        assert claimed is not None
        assert claimed.status == OutboxEventStatus.IN_FLIGHT

    # Session 2: restart, recover expired lease, reclaim and deliver
    hub = _IdempotentHubMock()
    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        recovered = outbox.recover_expired_leases(now_utc=t_after_expiry)
        assert len(recovered) == 1
        assert recovered[0].event_id == "evt-inflight-crash"
        assert recovered[0].status == OutboxEventStatus.RETRY
        assert recovered[0].attempt_count == 1
        assert recovered[0].claim_count == 1
        assert recovered[0].lease_id is None

        reclaimed = outbox.claim_event("worker-recovered", lease_duration_seconds=30.0, now_utc=t_after_expiry)
        assert reclaimed is not None
        delivered = outbox.deliver_claimed_event(reclaimed, hub, now_utc=t_after_expiry)
        assert delivered.status == OutboxEventStatus.DELIVERED
        assert delivered.ack_id == "ack-evt-inflight-crash"


def test_resilience_crash_after_remote_receipt_before_local_ack_idempotent_redelivery(tmp_path: Path) -> None:
    """Hub receives event, response is lost; Outbox retries with same event_id and Hub ACKs idempotently."""
    db_path = tmp_path / "resilience_lost_ack.db"
    t0 = datetime(2026, 11, 19, 16, 0, 0, tzinfo=timezone.utc)
    policy = RetryPolicy(max_attempts=3, base_delay_seconds=5.0, max_delay_seconds=30.0, jitter_ratio=0.0)
    hub = _IdempotentHubMock(drop_first_ack=True)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store, retry_policy=policy)
        outbox.enqueue_event(
            event_id="evt-lost-ack",
            event_type="TERMINATION_CONFIRMED",
            worker_id="worker-1",
            fencing_token=20,
            payload={"outcome": "TERMINATED"},
            now_utc=t0,
        )
        claimed1 = outbox.claim_event("worker-1", now_utc=t0)
        assert claimed1 is not None
        after_drop = outbox.deliver_claimed_event(claimed1, hub, now_utc=t0)
        assert after_drop.status == OutboxEventStatus.RETRY
        assert after_drop.attempt_count == 1
        # Hub already received the event once
        assert len(hub.received_events) == 1

    # Reopen after restart and retry once backoff elapses
    t1 = t0 + timedelta(seconds=10)
    with StateStore(db_path) as store:
        outbox = OutboxStore(store, retry_policy=policy)
        claimed2 = outbox.claim_event("worker-1", now_utc=t1)
        assert claimed2 is not None
        assert claimed2.event_id == "evt-lost-ack"
        delivered = outbox.deliver_claimed_event(claimed2, hub, now_utc=t1)
        assert delivered.status == OutboxEventStatus.DELIVERED
        assert delivered.ack_id == "ack-evt-lost-ack"
        # Hub processed 2 HTTP calls but deduplicated to 1 unique event
        assert hub.call_count == 2
        assert len(hub.received_events) == 1


def test_atomic_business_state_and_outbox_in_same_sqlite_transaction(tmp_path: Path) -> None:
    """Demonstrates local atomicity between StateStore and OutboxStore in one SQLite transaction."""
    db_path = tmp_path / "atomic_local.db"
    ident = CanonicalExecutionIdentity(
        execution_id="exec-atomic-1",
        attempt_id="att-atomic-1",
        job_id="job-atomic-1",
        tenant_id="tenant-1",
        channel_id="channel-1",
        worker_id="worker-1",
        fencing_token=15,
        pid=9876,
        process_create_time=133750000009999999,
        job_object_name="Local\\DCF_UPLOAD_att-atomic-1_exec-atomic-1",
        browser_user_data_dir="C:\\profiles\\channel-1",
    )

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        store.record_execution(ident, boot_time_utc="2026-11-19T00:00:00Z")

        # Enqueue outbox event inside the same transaction as another local SQLite write
        with store._transaction() as conn:
            outbox.enqueue_event(
                event_id="evt-atomic-1",
                event_type="EXECUTION_STARTED",
                worker_id=ident.worker_id,
                execution_id=ident.execution_id,
                attempt_id=ident.attempt_id,
                job_id=ident.job_id,
                fencing_token=ident.fencing_token,
                payload={"status": ExecutionStatus.RUNNING.value},
                conn=conn,
            )

        assert store.get_execution("exec-atomic-1") is not None
        assert outbox.get_event("evt-atomic-1") is not None
        assert all(r.status == IntegrityStatus.VALID for r in store.verify_integrity())
        assert all(r.status == IntegrityStatus.VALID for r in outbox.verify_integrity())


def test_integration_late_ack_rejected_while_new_owner_delivers_safely(tmp_path: Path) -> None:
    """End-to-end late ACK scenario across Worker A -> expiry -> Worker B."""
    import pytest
    from desktop_worker.watchdog.outbox import OutboxConflictError

    db_path = tmp_path / "late_ack_integration.db"
    t0 = datetime(2026, 11, 19, 17, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=45)
    hub = _IdempotentHubMock()

    with StateStore(db_path) as store:
        outbox = OutboxStore(store)
        outbox.enqueue_event(
            event_id="evt-late-int",
            event_type="TERMINATION_CONFIRMED",
            worker_id="worker-A",
            fencing_token=33,
            payload={"exit_code": 0},
            now_utc=t0,
        )
        claim_a = outbox.claim_event("worker-A", lease_duration_seconds=30.0, now_utc=t0)
        assert claim_a is not None

        # Lease expires, Watchdog recovers, Worker B claims
        recovered = outbox.recover_expired_leases(now_utc=t1)
        assert len(recovered) == 1
        claim_b = outbox.claim_event("worker-B", lease_duration_seconds=30.0, now_utc=t1)
        assert claim_b is not None
        assert claim_b.lease_id != claim_a.lease_id

        # Worker A receives a delayed ACK and tries to confirm -> REJECTED
        late_ack = HubAckResponse(accepted=True, event_id="evt-late-int", ack_id="ack-from-A")
        with pytest.raises(OutboxConflictError):
            outbox.mark_delivered(
                "evt-late-int",
                late_ack,
                lease_owner="worker-A",
                lease_id=claim_a.lease_id,
                expected_fencing_token=33,
                now_utc=t1,
            )

        # Event still belongs to Worker B
        mid = outbox.get_event("evt-late-int")
        assert mid is not None
        assert mid.status == OutboxEventStatus.IN_FLIGHT
        assert mid.lease_owner == "worker-B"
        assert mid.lease_id == claim_b.lease_id

        # Worker B delivers and confirms cleanly
        delivered = outbox.deliver_claimed_event(claim_b, hub, now_utc=t1)
        assert delivered.status == OutboxEventStatus.DELIVERED
        assert delivered.ack_id == "ack-evt-late-int"
        assert all(r.status == IntegrityStatus.VALID for r in outbox.verify_integrity())


def test_integration_repeated_crashes_exhaust_max_attempts_across_restarts(tmp_path: Path) -> None:
    """Repeated process crashes after claim cannot bypass max_attempts."""
    db_path = tmp_path / "crash_exhaust.db"
    t = datetime(2026, 11, 19, 18, 0, 0, tzinfo=timezone.utc)
    policy = RetryPolicy(max_attempts=2, base_delay_seconds=2.0, max_delay_seconds=10.0, jitter_ratio=0.0)

    with StateStore(db_path) as store:
        outbox = OutboxStore(store, retry_policy=policy)
        outbox.enqueue_event(
            event_id="evt-crash-cap",
            event_type="TERMINATION_CONFIRMED",
            worker_id="worker-1",
            payload={"job": "abc"},
            now_utc=t,
        )

    for i in range(1, 3):
        with StateStore(db_path) as store:
            outbox = OutboxStore(store, retry_policy=policy)
            claimed = outbox.claim_event(f"worker-run-{i}", lease_duration_seconds=10.0, now_utc=t)
            assert claimed is not None
            assert claimed.claim_count == i
        # Process crashes; time advances past lease expiry
        t = t + timedelta(seconds=20)
        with StateStore(db_path) as store:
            outbox = OutboxStore(store, retry_policy=policy)
            recovered = outbox.recover_expired_leases(now_utc=t)
            assert len(recovered) == 1
            if i == 1:
                assert recovered[0].status == OutboxEventStatus.RETRY
            else:
                assert recovered[0].status == OutboxEventStatus.DEAD_LETTER

    with StateStore(db_path) as store:
        outbox = OutboxStore(store, retry_policy=policy)
        final = outbox.get_event("evt-crash-cap")
        assert final is not None
        assert final.status == OutboxEventStatus.DEAD_LETTER
        assert final.attempt_count == 2
        assert final.claim_count == 2
        assert final.payload_json == '{"job":"abc"}'
        assert all(r.status == IntegrityStatus.VALID for r in outbox.verify_integrity())
