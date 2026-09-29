"""Notification constraints and transaction behavior in an isolated database."""

from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from db import ChallengeSubmission, NotificationOutbox, Role, User, db


@pytest.fixture
def session():
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_BINDS={"pond": "sqlite:///:memory:"},
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    with app.app_context():
        db.create_all(bind_key="pond")
        role = Role(role_name="sysadmin", role_level=3)
        db.session.add(role)
        db.session.flush()
        db.session.add_all([
            User(user_id=i, username="user" + str(i), display_name="User", role_id=role.role_id)
            for i in (1, 2)
        ])
        db.session.flush()
        db.session.add(ChallengeSubmission(submission_id=1, uploaded_by_user_id=1,
                                            title="Example", challenge_type="vm"))
        db.session.commit()
        yield db.session
        db.session.remove()


def add_notification(session, **overrides):
    values = dict(submission_id=1, recipient_user_id=1, event_type="needs_changes",
                  deduplication_key="event:1:user:1:in_app", message="Your submission needs a target VM image.")
    values.update(overrides)
    row = NotificationOutbox(**values)
    session.add(row)
    session.flush()
    return row


def test_defaults_and_relationships(session):
    row = add_notification(session)
    session.commit()
    assert row.channel == "in_app" and row.status == "pending"
    assert row.attempt_count == 0 and row.available_at and row.created_at
    assert row.sent_at is None and row.read_at is None
    assert row.submission.submission_id == 1 and row.recipient.user_id == 1


@pytest.mark.parametrize("values", [
    {"submission_id": 999}, {"recipient_user_id": 999}, {"channel": "email"},
    {"status": "unknown"}, {"attempt_count": -1}, {"message": None},
    {"event_type": None}, {"deduplication_key": None},
    {"status": "sent"}, {"status": "delivering"},
    {"status": "delivering", "lease_token": "claim"},
    {"status": "delivering", "lease_expires_at": datetime.now(timezone.utc)},
])
def test_invalid_notifications_rejected(session, values):
    with pytest.raises(IntegrityError):
        add_notification(session, **values)
    session.rollback()


def test_duplicate_event_rejected_after_delivery(session):
    add_notification(session, status="sent", sent_at=datetime.now(timezone.utc))
    session.commit()
    with pytest.raises(IntegrityError):
        add_notification(session)
    session.rollback()


def test_distinct_recipients_and_event_occurrences_allowed(session):
    first = add_notification(session)
    second = add_notification(session, recipient_user_id=2, deduplication_key="event:1:user:2:in_app")
    third = add_notification(session, deduplication_key="event:2:user:1:in_app")
    session.commit()
    assert len({first.notification_id, second.notification_id, third.notification_id}) == 3


def test_delivery_and_read_timestamps_are_separate(session):
    row = add_notification(session)
    row.status = "delivering"
    row.lease_token = "claim-1"
    row.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    row.attempt_count = 1
    session.commit()
    row.status = "sent"
    row.sent_at = datetime.now(timezone.utc)
    row.lease_token = None
    row.lease_expires_at = None
    session.commit()
    assert row.sent_at is not None and row.read_at is None
    row.read_at = datetime.now(timezone.utc)
    session.commit()
    assert row.status == "sent" and row.read_at is not None


def test_retry_retains_same_record_and_attempt_count(session):
    row = add_notification(session, status="failed", attempt_count=1, last_error="Temporary failure")
    session.commit()
    identifier = row.notification_id
    row.status = "pending"
    row.available_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    session.commit()
    assert row.notification_id == identifier and row.attempt_count == 1


@pytest.mark.parametrize("model,key", [(ChallengeSubmission, "submission_id"), (User, "user_id")])
def test_referenced_records_cannot_be_deleted(session, model, key):
    # User 2 has no submission ownership links; the outbox is what protects it.
    row = add_notification(session, recipient_user_id=2)
    session.commit()
    identifier = 2 if model is User else 1
    with pytest.raises(IntegrityError):
        session.execute(delete(model).where(getattr(model, key) == identifier))
        session.flush()
    session.rollback()
    assert session.get(NotificationOutbox, row.notification_id) is not None


def test_submission_result_and_notification_roll_back_together(session):
    submission = session.get(ChallengeSubmission, 1)
    submission.content_digest = "a" * 64
    submission.submitted_at = datetime.now(timezone.utc)
    submission.status = "needs_changes"
    row = add_notification(session)
    identifier = row.notification_id
    session.rollback()
    assert session.get(ChallengeSubmission, 1).status == "draft"
    assert session.get(NotificationOutbox, identifier) is None


def test_raw_sql_defaults_and_duplicate_protection(session):
    sql = text("INSERT INTO notification_outbox (submission_id,recipient_user_id,event_type,deduplication_key,message) "
               "VALUES (1,1,'needs_changes','raw-event','Missing file')")
    with db.engines["pond"].begin() as conn:
        conn.execute(sql)
        row = conn.execute(text("SELECT channel,status,attempt_count,created_at FROM notification_outbox")).one()
        assert tuple(row[:3]) == ("in_app", "pending", 0) and row[3]
    with pytest.raises(IntegrityError):
        with db.engines["pond"].begin() as conn:
            conn.execute(sql)
