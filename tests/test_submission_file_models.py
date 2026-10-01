"""File-inventory constraints tested with the actual registered Pond models."""

import pytest
from flask import Flask
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from db import ChallengeSubmission, Role, SubmissionFile, User, db


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


def add_file(session, **overrides):
    values = dict(submission_id=1, file_role="image", logical_path="vms/target.qcow2",
                  original_filename="target.qcow2", storage_key="quarantine/object-1",
                  size_bytes=1024, sha256="a" * 64)
    values.update(overrides)
    row = SubmissionFile(**values)
    session.add(row)
    session.flush()
    return row


def test_file_attaches_to_submission_with_pending_defaults(session):
    row = add_file(session)
    session.commit()
    assert row.submission.submission_id == 1
    assert row.validation_status == "pending"
    assert row.validated_at is None and row.detected_media_type is None
    assert row.created_at is not None


@pytest.mark.parametrize("values", [
    {"submission_id": 999}, {"size_bytes": -1}, {"sha256": "short"},
    {"sha256": "g" * 64}, {"sha256": "A" * 64}, {"sha256": "a" * 63 + "\n"},
    {"validation_status": "approved"}, {"storage_key": None},
])
def test_invalid_inventory_records_rejected(session, values):
    with pytest.raises(IntegrityError):
        add_file(session, **values)
    session.rollback()


def test_duplicate_path_within_submission_rejected(session):
    add_file(session)
    session.commit()
    with pytest.raises(IntegrityError):
        add_file(session, storage_key="quarantine/object-2")
    session.rollback()


def test_revisions_can_have_same_path_and_hash_but_separate_storage(session):
    first = add_file(session)
    second = add_file(session, submission_id=2, storage_key="quarantine/object-2")
    session.commit()
    assert first.logical_path == second.logical_path and first.sha256 == second.sha256
    assert first.file_id != second.file_id


def test_storage_object_cannot_be_shared_by_two_records(session):
    add_file(session)
    session.commit()
    with pytest.raises(IntegrityError):
        add_file(session, submission_id=2)
    session.rollback()


def test_multiple_vm_images_allowed_in_one_submission(session):
    add_file(session)
    other = add_file(session, logical_path="vms/attacker.qcow2", storage_key="quarantine/object-2")
    session.commit()
    assert other.file_role == "image"


def test_parent_cannot_be_deleted_while_files_exist(session):
    row = add_file(session)
    session.commit()
    with pytest.raises(IntegrityError):
        session.execute(delete(ChallengeSubmission).where(ChallengeSubmission.submission_id == 1))
        session.flush()
    session.rollback()
    assert session.get(SubmissionFile, row.file_id) is not None
    assert session.get(ChallengeSubmission, 1) is not None


def test_raw_sql_defaults_and_duplicate_path_constraint(session):
    values = dict(digest="a" * 64)
    sql = text("INSERT INTO submission_files (submission_id,file_role,logical_path,original_filename,"
               "storage_key,size_bytes,sha256) VALUES (1,'instructions','instructions.md',"
               "'instructions.md','raw-object',0,:digest)")
    with db.engines["pond"].begin() as conn:
        conn.execute(sql, values)
        assert conn.execute(text("SELECT validation_status FROM submission_files")).scalar_one() == "pending"
    with pytest.raises(IntegrityError):
        with db.engines["pond"].begin() as conn:
            conn.execute(text("INSERT INTO submission_files (submission_id,file_role,logical_path,"
                              "original_filename,storage_key,size_bytes,sha256) VALUES "
                              "(1,'instructions','instructions.md','copy.md','another-object',0,:digest)"), values)
