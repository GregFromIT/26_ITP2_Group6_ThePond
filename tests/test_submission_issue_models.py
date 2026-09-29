"""Issue ownership and history constraints using the registered Pond models."""

from datetime import datetime, timezone

import pytest
from flask import Flask
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from db import ChallengeSubmission, Role, SubmissionFile, SubmissionIssue, SubmissionJob, User, db


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
        for i in (1, 2):
            db.session.add(ChallengeSubmission(submission_id=i, uploaded_by_user_id=1,
                                               title="Example", challenge_type="vm"))
        db.session.flush()
        for i in (1, 2):
            db.session.add(SubmissionFile(file_id=i, submission_id=i, file_role="image",
                                          logical_path="target.qcow2", original_filename="target.qcow2",
                                          storage_key="object-" + str(i), size_bytes=100, sha256="a" * 64))
            db.session.add(SubmissionJob(job_id=i, submission_id=i, requested_by_user_id=1,
                                         action="validate", idempotency_key="validate:" + str(i)))
        db.session.commit()
        yield db.session
        db.session.remove()


def add_issue(session, **overrides):
    values = dict(submission_id=1, job_id=1, severity="error", error_code="MISSING_IMAGE",
                  field_path="vms.target.image", message="Target VM image is required.")
    values.update(overrides)
    row = SubmissionIssue(**values)
    session.add(row)
    session.flush()
    return row


def test_missing_file_issue_needs_no_file_record(session):
    row = add_issue(session)
    session.commit()
    assert row.file_id is None and row.file is None
    assert row.job.job_id == 1 and row.submission.submission_id == 1
    assert row.created_at and row.resolved_at is None and row.resolved_by is None


def test_existing_file_issue_and_reviewer_relationships(session):
    row = add_issue(session, file_id=1, error_code="INVALID_FORMAT")
    session.commit()
    assert row.file.file_id == 1 and row.job.submission_id == row.submission_id
    row.resolved_by_user_id = 2
    row.resolved_at = datetime.now(timezone.utc)
    row.resolution_note = "Corrected in replacement submission; retained as history."
    session.commit()
    assert row.resolved_by.user_id == 2
    assert row.message == "Target VM image is required."


@pytest.mark.parametrize("values", [
    {"submission_id": 999}, {"job_id": 999}, {"file_id": 999},
    {"job_id": 2}, {"file_id": 2}, {"submission_id": 2},
    {"severity": "fatal"}, {"job_id": None}, {"message": None},
    {"error_code": None}, {"resolved_by_user_id": 999},
])
def test_invalid_findings_rejected(session, values):
    with pytest.raises(IntegrityError):
        add_issue(session, **values)
    session.rollback()


@pytest.mark.parametrize("severity", ["error", "warning", "info"])
def test_all_severities_allowed(session, severity):
    row = add_issue(session, severity=severity)
    session.commit()
    assert row.severity == severity


def test_automated_resolution_can_omit_human_reviewer(session):
    row = add_issue(session, resolved_at=datetime.now(timezone.utc),
                    resolution_note="Revalidation confirmed correction.")
    session.commit()
    assert row.resolved_at and row.resolved_by_user_id is None


@pytest.mark.parametrize("model,key", [(SubmissionFile, "file_id"), (SubmissionJob, "job_id"),
                                       (ChallengeSubmission, "submission_id")])
def test_referenced_records_cannot_be_deleted(session, model, key):
    row = add_issue(session, file_id=1)
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(delete(model).where(getattr(model, key) == 1))
        session.flush()
    session.rollback()
    assert session.get(SubmissionIssue, row.issue_id) is not None


def test_resolver_deletion_restricted(session):
    add_issue(session, resolved_by_user_id=2, resolved_at=datetime.now(timezone.utc))
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(delete(User).where(User.user_id == 2))
        session.flush()
    session.rollback()


def test_new_validation_run_preserves_earlier_findings(session):
    first = add_issue(session)
    session.get(SubmissionJob, 1).status = "succeeded"
    session.flush()
    job = SubmissionJob(submission_id=1, requested_by_user_id=1, action="validate",
                        idempotency_key="validate:1:rerun")
    session.add(job)
    session.flush()
    second = add_issue(session, job_id=job.job_id)
    session.commit()
    assert first.issue_id != second.issue_id and first.job_id != second.job_id
    assert session.get(SubmissionIssue, first.issue_id) is not None


def test_raw_sql_enforces_composite_file_ownership(session):
    with pytest.raises(IntegrityError):
        with db.engines["pond"].begin() as conn:
            conn.execute(text("INSERT INTO submission_issues (submission_id,job_id,file_id,severity,error_code,message) "
                              "VALUES (1,1,2,'error','INVALID','Wrong owner')"))
    with db.engines["pond"].begin() as conn:
        conn.execute(text("INSERT INTO submission_issues (submission_id,job_id,severity,error_code,message) "
                          "VALUES (1,1,'error','MISSING','File required')"))
        assert conn.execute(text("SELECT created_at FROM submission_issues")).scalar_one() is not None
