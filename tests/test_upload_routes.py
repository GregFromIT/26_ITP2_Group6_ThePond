"""Authenticated web/intake integration against temporary storage and SQLite."""

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys

import pytest
from flask import Flask
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pond-sec"))
from app import create_app
from db import (ChallengeSubmission, NotificationOutbox, Role, SubmissionFile, SubmissionJob,
                User, db)
from challenge_ingestion.notifications import NotificationService
from challenge_ingestion.storage import QuarantineStorage
from challenge_ingestion.worker import ValidationWorker


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(Flask, "auto_find_instance_path", lambda self: str(tmp_path / "instance"))
    app = create_app({"TESTING": True, "SECRET_KEY": "test-only", "FORCE_HTTPS": False,
        "SQLALCHEMY_BINDS": {"pond": "sqlite:///" + str(tmp_path / "web.db")},
        "UPLOAD_QUARANTINE_ROOT": str(tmp_path / "quarantine"), "UPLOAD_MAX_IMAGE_BYTES": 1024,
        "UPLOAD_USER_QUOTA_BYTES": 10000, "UPLOAD_TOTAL_QUOTA_BYTES": 20000})
    with app.app_context():
        from db.init_database import initialise_database
        initialise_database()
        roles = {r.role_name: r.role_id for r in db.session.scalars(select(Role))}
        for i, role in [(1, "manager"), (2, "manager"), (3, "sysadmin"), (4, "user")]:
            db.session.add(User(user_id=i, role_id=roles[role], username=f"user{i}", display_name="Test"))
        db.session.commit()
    manifest = json.loads((Path(__file__).resolve().parents[1] / "examples/challenge_uploads/scored-vm-image.json").read_text())
    yield app, manifest, tmp_path
    with app.app_context():
        db.session.remove()
        db.engines["pond"].dispose()


def client(app, user=1):
    c = app.test_client()
    with c.session_transaction() as session:
        if user is not None:
            session["user_id"] = user
        session["_csrf"] = "test-csrf"
    return c


def create(c, manifest):
    result = c.post("/admin/challenge-submissions", data={"_csrf": "test-csrf",
                    "manifest": (io.BytesIO(json.dumps(manifest).encode()), "challenge.json")})
    assert result.status_code == 303, result.data
    return int(result.location.rsplit("/", 1)[-1])


def post(c, identifier, action):
    return c.post(f"/admin/challenge-submissions/{identifier}/{action}", data={"_csrf": "test-csrf"})


def upload(c, identifier, data=b"QFI\xfbtest disk", path="images/target.qcow2", token="test-csrf"):
    return c.post(f"/admin/challenge-submissions/{identifier}/files", query_string={"path": path},
                  data=data, content_type="application/octet-stream",
                  headers={"X-CSRF-Token": token, "X-Upload-Filename": "target.qcow2"})


def test_pages_create_upload_submit_worker_and_notification_flow(env):
    app, manifest, root = env
    c = client(app)
    assert c.get("/admin/challenge-submissions/new").status_code == 200
    identifier = create(c, manifest)
    assert upload(c, identifier).status_code == 201
    assert c.get(f"/admin/challenge-submissions/{identifier}").status_code == 200
    assert post(c, identifier, "submit").status_code == 303
    assert post(c, identifier, "submit").status_code == 303
    with app.app_context(), QuarantineStorage(root / "quarantine") as storage:
        assert len(list(db.session.scalars(select(SubmissionJob)))) == 1
        db.session.remove()
        ValidationWorker(db.engines["pond"], storage, worker_id="test", inspect_image=lambda *_: True).run_once()
        NotificationService(db.engines["pond"]).deliver_pending()
    response = c.get(f"/admin/challenge-submissions/{identifier}/status")
    assert response.json["status"] == "ready_for_review"
    assert b"ready for review" in c.get("/notifications").data


def test_anonymous_and_students_cannot_upload(env):
    app, _, _ = env
    assert client(app, None).get("/admin/challenge-submissions").status_code == 302
    assert client(app, 4).get("/admin/challenge-submissions").status_code == 403
    assert upload(client(app, 4), 1).status_code == 403


def test_other_moderator_denied_admin_allowed(env):
    app, manifest, _ = env
    identifier = create(client(app), manifest)
    other = client(app, 2)
    for endpoint in ("", "/status"):
        assert other.get(f"/admin/challenge-submissions/{identifier}{endpoint}").status_code == 404
    assert upload(other, identifier).status_code == 404
    assert post(other, identifier, "submit").status_code == 404
    assert b"Practice target" not in other.get("/admin/challenge-submissions").data
    assert client(app, 3).get(f"/admin/challenge-submissions/{identifier}").status_code == 200


def test_inactive_staff_blocked(env):
    app, _, _ = env
    c = client(app)
    with app.app_context():
        db.session.get(User, 1).is_active = False
        db.session.commit()
    assert c.get("/admin/challenge-submissions").status_code == 403
    assert c.get("/notifications").status_code == 403


def test_csrf_on_create_upload_submit_revision_and_read(env):
    app, manifest, _ = env
    c = client(app)
    identifier = create(c, manifest)
    assert c.post("/admin/challenge-submissions", data={}).status_code == 400
    assert upload(c, identifier, token="wrong").status_code == 400
    for action in ("submit", "revise", "manifest", "files/1/remove"):
        assert c.post(f"/admin/challenge-submissions/{identifier}/{action}").status_code == 400
    assert c.post("/notifications/1/read").status_code == 400


def test_limits_and_unexpected_paths(env):
    app, manifest, _ = env
    c = client(app)
    identifier = create(c, manifest)
    assert upload(c, identifier, data=b"a" * 1025).status_code == 413
    assert upload(c, identifier, path="../outside").status_code == 400
    assert c.post(f"/admin/challenge-submissions/{identifier}/files", data={"_csrf": "test-csrf"}).status_code == 415
    assert upload(c, identifier).status_code == 201
    assert upload(c, identifier).status_code == 400


def test_user_quota_rejects_without_orphan_file(env):
    app, manifest, root = env
    app.config["UPLOAD_USER_QUOTA_BYTES"] = 3
    c = client(app)
    identifier = create(c, manifest)
    assert upload(c, identifier).status_code == 400
    assert not list((root / "quarantine").glob("*.blob"))


def test_frozen_revision_cannot_change(env):
    app, manifest, _ = env
    c = client(app)
    identifier = create(c, manifest)
    post(c, identifier, "submit")
    assert upload(c, identifier).status_code == 400
    result = c.post(f"/admin/challenge-submissions/{identifier}/manifest", data={"_csrf": "test-csrf",
                    "manifest": (io.BytesIO(json.dumps(manifest).encode()), "challenge.json")})
    assert result.status_code == 400


def test_missing_image_issue_and_correction_preserve_history(env):
    app, manifest, root = env
    c = client(app)
    identifier = create(c, manifest)
    post(c, identifier, "submit")
    with app.app_context(), QuarantineStorage(root / "quarantine") as storage:
        ValidationWorker(db.engines["pond"], storage, worker_id="test").run_once()
    page = c.get(f"/admin/challenge-submissions/{identifier}")
    assert b"MISSING IMAGE" in page.data
    response = post(c, identifier, "revise")
    assert response.status_code == 303
    revised = int(response.location.rsplit("/", 1)[-1])
    assert revised != identifier
    assert upload(c, revised).status_code == 201
    with app.app_context():
        assert db.session.get(ChallengeSubmission, identifier).status == "needs_changes"
        assert db.session.get(ChallengeSubmission, revised).supersedes_submission_id == identifier


def test_revision_copies_existing_files_independently(env):
    app, manifest, root = env
    c = client(app)
    identifier = create(c, manifest)
    upload(c, identifier)
    post(c, identifier, "submit")
    with app.app_context(), QuarantineStorage(root / "quarantine") as storage:
        ValidationWorker(db.engines["pond"], storage, worker_id="test", inspect_image=lambda *_: False).run_once()
    revised = int(post(c, identifier, "revise").location.rsplit("/", 1)[-1])
    with app.app_context():
        files = list(db.session.scalars(select(SubmissionFile).order_by(SubmissionFile.file_id)))
        assert len(files) == 2 and files[0].storage_key != files[1].storage_key
        file_id = files[1].file_id
    assert post(c, revised, f"files/{file_id}/remove").status_code == 303
    with app.app_context():
        assert len(list(db.session.scalars(select(SubmissionFile)))) == 1


def test_notifications_are_scoped_and_mark_read_ignores_supplied_user_id(env):
    app, manifest, _ = env
    c = client(app)
    identifier = create(c, manifest)
    with app.app_context():
        db.session.add(NotificationOutbox(notification_id=1, submission_id=identifier, recipient_user_id=1,
            event_type="needs_changes", deduplication_key="test", message="private message", status="sent", sent_at=datetime.now(timezone.utc)))
        db.session.commit()
    assert b"private message" in c.get("/notifications").data
    other = client(app, 2)
    assert b"private message" not in other.get("/notifications?user_id=1").data
    assert other.post("/notifications/1/read", data={"_csrf": "test-csrf", "user_id": 1}).status_code == 404
    assert c.post("/notifications/1/read", data={"_csrf": "test-csrf"}).status_code == 303


def test_escape_submission_titles(env):
    app, manifest, _ = env
    manifest["title"] = "<script>alert(1)</script>"
    c = client(app)
    identifier = create(c, manifest)
    page = c.get(f"/admin/challenge-submissions/{identifier}").data
    assert b"<script>alert(1)</script>" not in page
    assert b"&lt;script&gt;" in page


def test_missing_configuration_and_invalid_manifest(env):
    app, manifest, _ = env
    c = client(app)
    response = c.post("/admin/challenge-submissions", data={"_csrf": "test-csrf", "manifest": (io.BytesIO(b"{}"), "challenge.json")})
    assert response.status_code == 400
    app.config["UPLOAD_QUARANTINE_ROOT"] = None
    response = c.post("/admin/challenge-submissions", data={"_csrf": "test-csrf", "manifest": (io.BytesIO(json.dumps(manifest).encode()), "challenge.json")})
    assert response.status_code == 503


def test_intake_lock_rejects_competing_upload_without_writing(env):
    import fcntl
    app, manifest, root = env
    c = client(app)
    identifier = create(c, manifest)
    with (root / "quarantine" / ".intake.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert upload(c, identifier).status_code == 409
    assert not list((root / "quarantine").glob("*.blob"))
    assert upload(c, identifier).status_code == 201


def test_global_quota_counts_other_uploaders(env):
    app, manifest, _ = env
    app.config["UPLOAD_TOTAL_QUOTA_BYTES"] = 20
    first, second = client(app), client(app, 2)
    one, two = create(first, manifest), create(second, manifest)
    assert upload(first, one).status_code == 201
    assert upload(second, two).status_code == 400


def test_database_insert_failure_cleans_new_storage_object(env):
    from sqlalchemy import event
    app, manifest, root = env
    c = client(app)
    identifier = create(c, manifest)
    with app.app_context():
        engine = db.engines["pond"]
    def fail(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO submission_files"):
            raise RuntimeError("Simulated database failure")
    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="Simulated database failure"):
            upload(c, identifier)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert not list((root / "quarantine").glob("*.blob"))
    with app.app_context():
        assert db.session.scalar(select(SubmissionFile)) is None


def test_public_static_storage_configuration_rejected(env):
    app, manifest, _ = env
    c = client(app)
    app.config["UPLOAD_QUARANTINE_ROOT"] = str(Path(app.static_folder) / "quarantine")
    result = c.post("/admin/challenge-submissions", data={"_csrf": "test-csrf",
                  "manifest": (io.BytesIO(json.dumps(manifest).encode()), "challenge.json")})
    assert result.status_code == 503


def test_incomplete_stream_removed_by_intake(env):
    from challenge_ingestion.intake import IntakeError, IntakeService
    app, manifest, root = env
    c = client(app)
    identifier = create(c, manifest)
    with app.app_context(), QuarantineStorage(root / "quarantine") as storage:
        service = IntakeService(db.engines["pond"], storage)
        with pytest.raises(IntakeError, match="incomplete"):
            service.upload(1, identifier, "images/target.qcow2", "target.qcow2", io.BytesIO(b"tiny"), 100)
    assert not list((root / "quarantine").glob("*.blob"))
