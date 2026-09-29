"""In-app delivery/inbox integration tests; all data is temporary."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from flask import Flask
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from db import ChallengeSubmission, NotificationOutbox, Role, User, db
from challenge_ingestion.notifications import NotificationService


@pytest.fixture
def env(tmp_path):
    app = Flask(__name__)
    app.config["SQLALCHEMY_BINDS"] = {"pond": "sqlite:///" + str(tmp_path / "notifications.db")}
    db.init_app(app)
    with app.app_context():
        db.create_all(bind_key="pond")
        engine = db.engines["pond"]
        now = {"value": datetime(2026, 9, 28, 0, 0)}
        with Session(engine) as session:
            session.add(Role(role_id=1, role_name="sysadmin", role_level=3))
            session.flush()
            session.add_all([User(user_id=i, role_id=1, username=f"user{i}", display_name="User") for i in (1, 2)])
            session.flush()
            session.add(ChallengeSubmission(submission_id=1, uploaded_by_user_id=1, title="Test", challenge_type="vm"))
            session.flush()
            session.add_all([NotificationOutbox(notification_id=i, submission_id=1, recipient_user_id=recipient,
                event_type="needs_changes", deduplication_key=f"event-{i}", message="Review submission issues.",
                available_at=now["value"]) for i, recipient in [(1, 1), (2, 2), (3, 1)]])
            session.commit()
        service = NotificationService(engine, clock=lambda: now["value"])
        yield service, engine, now
        db.session.remove()
        engine.dispose()


def test_delivery_once_and_inbox_recipient_scope(env):
    service, engine, _ = env
    assert service.inbox(actor_user_id=1) == ()
    result = service.deliver_pending()
    assert result.sent == 3 and result.failed == 0
    assert service.deliver_pending().sent == 0
    assert [n.notification_id for n in service.inbox(actor_user_id=1)] == [3, 1]
    assert [n.notification_id for n in service.inbox(actor_user_id=2)] == [2]
    assert service.unread_count(actor_user_id=1) == 2
    with Session(engine) as session:
        assert all(row.attempt_count == 1 for row in session.scalars(select(NotificationOutbox)))


def test_pending_foreign_and_missing_records_cannot_be_marked_read(env):
    service, _, _ = env
    assert not service.mark_read(1, actor_user_id=1)
    service.deliver_pending()
    assert not service.mark_read(2, actor_user_id=1)
    assert not service.mark_read(999, actor_user_id=1)
    assert service.unread_count(actor_user_id=2) == 1


def test_acknowledgement_idempotent_and_preserves_submission(env):
    service, engine, now = env
    service.deliver_pending()
    assert service.mark_read(1, actor_user_id=1)
    first = service.inbox(actor_user_id=1)[1].read_at
    now["value"] += timedelta(seconds=10)
    assert service.mark_read(1, actor_user_id=1)
    assert service.inbox(actor_user_id=1)[1].read_at == first
    assert service.unread_count(actor_user_id=1) == 1
    assert [n.notification_id for n in service.inbox(actor_user_id=1, unread_only=True)] == [3]
    with Session(engine) as session:
        assert session.get(ChallengeSubmission, 1).status == "draft"


def test_pagination_and_safe_output_fields(env):
    service, _, _ = env
    service.deliver_pending()
    first = service.inbox(actor_user_id=1, limit=1)
    second = service.inbox(actor_user_id=1, limit=1, before_id=first[0].notification_id)
    assert first[0].notification_id == 3 and second[0].notification_id == 1
    assert not hasattr(first[0], "last_error") and not hasattr(first[0], "deduplication_key")


def test_two_deliverers_do_not_duplicate_delivery(env):
    service, engine, now = env
    other = NotificationService(engine, clock=lambda: now["value"])
    barrier = Barrier(2)
    def deliver(s):
        barrier.wait()
        return s.deliver_pending()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(deliver, [service, other]))
    assert sum(r.sent for r in results) == 3
    with Session(engine) as session:
        assert all(row.attempt_count == 1 for row in session.scalars(select(NotificationOutbox)))


def test_expired_delivery_lease_recovered_and_live_lease_respected(env):
    service, engine, now = env
    with Session(engine) as session:
        for identifier, offset in [(1, -1), (2, 10)]:
            row = session.get(NotificationOutbox, identifier)
            row.status, row.lease_token = "delivering", "claim"
            row.lease_expires_at = now["value"] + timedelta(seconds=offset)
            row.attempt_count = 1
        session.commit()
    assert service.deliver_pending().sent == 2
    assert service.inbox(actor_user_id=2) == ()
    now["value"] += timedelta(seconds=11)
    assert service.deliver_pending().sent == 1
    with Session(engine) as session:
        row = session.get(NotificationOutbox, 1)
        assert row.attempt_count == 2 and row.lease_token is None


def test_limit_and_future_availability(env):
    service, engine, now = env
    with Session(engine) as session:
        session.get(NotificationOutbox, 1).available_at = now["value"] + timedelta(seconds=60)
        session.commit()
    assert service.deliver_pending(limit=1).sent == 1
    assert service.deliver_pending(limit=1).sent == 1
    assert service.deliver_pending().sent == 0
    now["value"] += timedelta(seconds=60)
    assert service.deliver_pending().sent == 1


def test_inactive_recipient_fails_delivery_and_loses_inbox_access(env):
    service, engine, _ = env
    with Session(engine) as session:
        session.get(User, 1).is_active = False
        session.commit()
    result = service.deliver_pending()
    assert result.failed == 2 and result.sent == 1
    for action in [lambda: service.inbox(actor_user_id=1), lambda: service.unread_count(actor_user_id=1),
                   lambda: service.mark_read(1, actor_user_id=1)]:
        with pytest.raises(PermissionError):
            action()
    assert service.deliver_pending().failed == 0


def test_attempt_limit_and_failed_rows_not_auto_requeued(env):
    service, engine, _ = env
    with Session(engine) as session:
        session.get(NotificationOutbox, 1).attempt_count = 3
        session.get(NotificationOutbox, 2).status = "failed"
        session.commit()
    result = service.deliver_pending()
    assert result.sent == 1 and result.failed == 1
    assert service.deliver_pending().sent == 0
    assert [row.notification_id for row in service.inbox(actor_user_id=1)] == [3]


def test_database_failure_rolls_back_delivery_batch(env):
    service, engine, _ = env
    def fail(conn, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE notification_outbox"):
            raise RuntimeError("Simulated database failure")
    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError):
            service.deliver_pending()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    with Session(engine) as session:
        assert all(row.status == "pending" and row.attempt_count == 0 for row in session.scalars(select(NotificationOutbox)))
    assert service.deliver_pending().sent == 3


@pytest.mark.parametrize("limit", [0, -1, True, 1001, "10"])
def test_invalid_delivery_limits(env, limit):
    with pytest.raises(ValueError):
        env[0].deliver_pending(limit=limit)


@pytest.mark.parametrize("actor", [True, 0, "1"])
def test_invalid_actor_identity(env, actor):
    with pytest.raises(ValueError):
        env[0].inbox(actor_user_id=actor)


def test_unknown_actor_denied(env):
    with pytest.raises(PermissionError):
        env[0].inbox(actor_user_id=999)
