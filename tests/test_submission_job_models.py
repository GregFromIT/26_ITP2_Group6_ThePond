"""Submission queue constraints against an isolated SQLite database."""

from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from db import ChallengeSubmission, Role, SubmissionJob, User, db


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
        db.session.add(User(user_id=1, username="uploader", display_name="Uploader", role_id=role.role_id))
        db.session.flush()
        db.session.add_all([
            ChallengeSubmission(submission_id=i, uploaded_by_user_id=1,
                                title="Example", challenge_type="vm")
            for i in (1, 2)
        ])
        db.session.commit()
        yield db.session
        db.session.remove()


def add_job(session, **overrides):
    values = dict(submission_id=1, requested_by_user_id=1, action="validate", idempotency_key="validate:1")
    values.update(overrides)
    row = SubmissionJob(**values)
    session.add(row)
    session.flush()
    return row


def test_defaults_and_relationships(session):
    row = add_job(session)
    other = add_job(session, submission_id=2, idempotency_key="validate:2")
    session.commit()
    assert row.status == "queued" and row.attempt_count == 0 and row.max_attempts == 3
    assert row.available_at and row.created_at
    assert row.submission.submission_id == 1 and row.requested_by.username == "uploader"
    assert row.resource_inventory_json == []
    row.resource_inventory_json = [{"node": "lab", "vmid": 1201, "owned": True}]
    session.commit()
    session.expire_all()
    assert row.resource_inventory_json[0]["vmid"] == 1201
    assert other.resource_inventory_json == []


@pytest.mark.parametrize("values", [
    {"submission_id": 999}, {"requested_by_user_id": 999}, {"action": "execute"},
    {"status": "approved"}, {"attempt_count": -1}, {"max_attempts": 0},
    {"resource_inventory_json": {}}, {"resource_inventory_json": None},
    {"status": "running"},
    {"status": "running", "locked_by": "worker", "lease_token": "token"},
])
def test_invalid_job_rejected(session, values):
    with pytest.raises(IntegrityError):
        add_job(session, **values)
    session.rollback()


@pytest.mark.parametrize("active_status", ["queued", "running", "retry_wait"])
def test_one_active_job_per_submission_across_actions(session, active_status):
    add_job(session, status=active_status, locked_by="worker", lease_token="token",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    session.commit()
    with pytest.raises(IntegrityError):
        add_job(session, action="publish", idempotency_key="publish:1")
    session.rollback()


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
def test_history_retained_when_new_job_queued(session, terminal_status):
    previous = add_job(session, status=terminal_status, completed_at=datetime.now(timezone.utc))
    session.commit()
    current = add_job(session, idempotency_key="validate:1:new-run")
    session.commit()
    assert current.job_id != previous.job_id
    assert session.get(SubmissionJob, previous.job_id).status == terminal_status


def test_duplicate_request_key_rejected_even_after_completion(session):
    add_job(session, status="succeeded")
    session.commit()
    with pytest.raises(IntegrityError):
        add_job(session, submission_id=2)
    session.rollback()


def test_running_job_keeps_retry_history(session):
    row = add_job(session)
    row.status = "running"
    row.locked_by = "worker-1"
    row.lease_token = "claim-1"
    row.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    row.attempt_count = 1
    session.commit()
    row.status = "retry_wait"
    row.error_code = "IMPORT_TIMEOUT"
    row.lease_token = None
    row.locked_by = None
    row.lease_expires_at = None
    session.commit()
    assert row.attempt_count == 1 and row.error_code == "IMPORT_TIMEOUT"


def test_submission_and_requester_deletion_restricted(session):
    row = add_job(session)
    session.commit()
    for model, condition in [(ChallengeSubmission, ChallengeSubmission.submission_id == 1),
                             (User, User.user_id == 1)]:
        with pytest.raises(IntegrityError):
            session.execute(delete(model).where(condition))
            session.flush()
        session.rollback()
    assert session.get(SubmissionJob, row.job_id) is not None


def test_direct_sql_uses_defaults_and_enforces_active_job_limit(session):
    with db.engines["pond"].begin() as conn:
        conn.execute(text("INSERT INTO submission_jobs (submission_id,requested_by_user_id,action,idempotency_key) "
                          "VALUES (1,1,'validate','raw:1')"))
        row = conn.execute(text("SELECT status,attempt_count,max_attempts,resource_inventory_json FROM submission_jobs")).one()
        assert tuple(row) == ("queued", 0, 3, "[]")
    with pytest.raises(IntegrityError):
        with db.engines["pond"].begin() as conn:
            conn.execute(text("INSERT INTO submission_jobs (submission_id,requested_by_user_id,action,idempotency_key) "
                              "VALUES (1,1,'cleanup','raw:2')"))
