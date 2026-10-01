"""Database integration tests; use only a temporary in-memory Pond database."""

from datetime import datetime, timezone

import pytest
from flask import Flask
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from db import Challenge, ChallengeSubmission, Role, User, db


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
            User(user_id=1, username="uploader", display_name="Uploader", role_id=role.role_id),
            User(user_id=2, username="reviewer", display_name="Reviewer", role_id=role.role_id),
        ])
        db.session.commit()
        yield db.session
        db.session.remove()


def draft(session, **overrides):
    values = dict(uploaded_by_user_id=1, title="Example", challenge_type="vm")
    values.update(overrides)
    row = ChallengeSubmission(**values)
    session.add(row)
    session.flush()
    return row


def finalized():
    return dict(content_digest="a" * 64, submitted_at=datetime.now(timezone.utc))


def approved():
    return dict(**finalized(), approved_by_user_id=2, approved_at=datetime.now(timezone.utc))


def test_draft_defaults_and_distinct_user_relationships(session):
    row = draft(session)
    other = draft(session)
    assert row.status == "draft" and row.manifest_json == {}
    assert row.manifest_json is not other.manifest_json
    assert row.schema_version == 1 and row.created_at and row.updated_at
    for field, value in approved().items():
        setattr(row, field, value)
    row.status = "approved"
    session.commit()
    assert row.uploaded_by.username == "uploader"
    assert row.approved_by.username == "reviewer"


@pytest.mark.parametrize("values", [
    {"status": "unknown"}, {"schema_version": 0}, {"manifest_json": []},
    {"manifest_json": None}, {"uploaded_by_user_id": 999},
    {"content_digest": "g" * 64}, {"status": "validating"},
    {"status": "approved", **finalized()}, {"approved_by_user_id": 2},
    {"approved_at": datetime.now(timezone.utc)},
    {"status": "published", **approved()},
])
def test_invalid_records_are_rejected(session, values):
    with pytest.raises(IntegrityError):
        draft(session, **values)
    session.rollback()


def test_failed_submission_retained_when_revision_created(session):
    original = draft(session, status="needs_changes", manifest_json={"vm": "target"}, **finalized())
    revision = draft(session, supersedes_submission_id=original.submission_id)
    session.commit()
    assert revision.supersedes is original
    assert session.get(ChallengeSubmission, original.submission_id).status == "needs_changes"
    with pytest.raises(IntegrityError):
        session.execute(delete(ChallengeSubmission).where(ChallengeSubmission.submission_id == original.submission_id))
        session.flush()
    session.rollback()


def test_self_revision_rejected(session):
    row = draft(session)
    row.supersedes_submission_id = row.submission_id
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_publication_unique_and_references_preserved(session):
    challenge = Challenge(title="Live", description="Example", instructions="Start here")
    session.add(challenge)
    session.flush()
    row = draft(session, status="published", published_challenge_id=challenge.challenge_id,
                published_at=datetime.now(timezone.utc), **approved())
    session.commit()
    assert row.published_challenge is challenge
    with pytest.raises(IntegrityError):
        draft(session, status="published", published_challenge_id=challenge.challenge_id,
              published_at=datetime.now(timezone.utc), **approved())
    session.rollback()
    with pytest.raises(IntegrityError):
        session.execute(delete(Challenge).where(Challenge.challenge_id == challenge.challenge_id))
        session.flush()
    session.rollback()
    with pytest.raises(IntegrityError):
        session.execute(delete(User).where(User.user_id == 1))
        session.flush()
    session.rollback()


def test_raw_sql_also_enforces_constraints_and_defaults(session):
    engine = db.engines["pond"]
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO challenge_submissions (uploaded_by_user_id,title,challenge_type) VALUES (1,'Raw','vm')"))
        result = conn.execute(text("SELECT status,manifest_json,schema_version FROM challenge_submissions")).one()
        assert tuple(result) == ("draft", "{}", 1)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE challenge_submissions SET manifest_json='invalid json'"))
