"""Review service and authenticated routes using temporary data/fake inspection."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import delete, event, select

from test_upload_routes import env, client, create, post, upload
from db import (AuditLog, ChallengeSubmission, NotificationOutbox, SubmissionFile,
                SubmissionIssue, SubmissionJob, User, db)
from challenge_ingestion.review import ReviewService
from challenge_ingestion.storage import QuarantineStorage
from challenge_ingestion.worker import ValidationWorker


@pytest.fixture
def ready(env):
    app, manifest, root = env
    c = client(app)
    identifier = create(c, manifest)
    assert upload(c, identifier).status_code == 201
    assert post(c, identifier, "submit").status_code == 303
    with app.app_context(), QuarantineStorage(root / "quarantine") as storage:
        ValidationWorker(db.engines["pond"], storage, worker_id="test", inspect_image=lambda *_: True).run_once()
    return app, identifier, root


def review(c, app, identifier, decision="approve", **overrides):
    with app.app_context():
        row = db.session.get(ChallengeSubmission, identifier)
        job = db.session.scalar(select(SubmissionJob).where(SubmissionJob.submission_id == identifier,
                                SubmissionJob.action == "validate").order_by(SubmissionJob.job_id.desc()))
        data = dict(_csrf="test-csrf", decision=decision, content_digest=row.content_digest,
                    job_id=job.job_id, note="Reviewed the challenge.")
    data.update(overrides)
    return c.post(f"/admin/challenge-submissions/{identifier}/review", data=data)


def test_admin_approval_records_identity_digest_and_notification_without_publication(ready):
    app, identifier, _ = ready
    c = client(app, 3)
    assert review(c, app, identifier).status_code == 303
    with app.app_context():
        row = db.session.get(ChallengeSubmission, identifier)
        assert row.status == "approved" and row.approved_by_user_id == 3 and row.approved_at
        assert row.published_challenge_id is None
        audit = db.session.scalar(select(AuditLog).where(AuditLog.action == "submission_review"))
        assert audit.actor_user_id == 3 and audit.details_json["content_digest"] == row.content_digest
        notices = list(db.session.scalars(select(NotificationOutbox).where(NotificationOutbox.event_type == "approved")))
        assert len(notices) == 1 and notices[0].status == "pending" and notices[0].recipient_user_id == 1
        assert not db.session.scalar(select(SubmissionJob).where(SubmissionJob.action == "publish"))
    page = c.get(f"/admin/challenge-submissions/{identifier}")
    assert b"Publication is still pending" in page.data and b"Reviewed the challenge" in page.data


def test_moderator_student_anonymous_and_inactive_admin_denied(ready):
    app, identifier, _ = ready
    assert review(client(app, 1), app, identifier).status_code == 403
    assert review(client(app, 4), app, identifier).status_code == 403
    assert review(client(app, None), app, identifier).status_code == 302
    with app.app_context():
        db.session.get(User, 3).is_active = False
        db.session.commit()
    assert review(client(app, 3), app, identifier).status_code == 403


def test_csrf_and_actor_spoofing(ready):
    app, identifier, _ = ready
    c = client(app, 3)
    assert review(c, app, identifier, _csrf="wrong").status_code == 400
    assert review(c, app, identifier, actor_user_id=1, approved_by_user_id=1).status_code == 303
    with app.app_context():
        assert db.session.get(ChallengeSubmission, identifier).approved_by_user_id == 3


@pytest.mark.parametrize("overrides", [{"content_digest": "0" * 64}, {"job_id": 999},
                                      {"job_id": "oops"}, {"note": "x" * 2001}])
def test_stale_or_invalid_review_rejected(ready, overrides):
    app, identifier, _ = ready
    assert review(client(app, 3), app, identifier, **overrides).status_code == 400
    with app.app_context():
        assert db.session.get(ChallengeSubmission, identifier).status == "ready_for_review"


def test_mutated_manifest_cannot_be_approved(ready):
    app, identifier, _ = ready
    with app.app_context():
        row = db.session.get(ChallengeSubmission, identifier)
        row.manifest_json = {**row.manifest_json, "title": "Changed after validation"}
        db.session.commit()
    assert review(client(app, 3), app, identifier).status_code == 400


def test_latest_issue_blocks_even_if_marked_resolved(ready):
    app, identifier, _ = ready
    with app.app_context():
        job = db.session.scalar(select(SubmissionJob).where(SubmissionJob.submission_id == identifier))
        db.session.add(SubmissionIssue(submission_id=identifier, job_id=job.job_id, severity="error",
            error_code="EXAMPLE", message="Requires revalidation", resolved_at=datetime.now(timezone.utc)))
        db.session.commit()
    assert review(client(app, 3), app, identifier).status_code == 400


def test_historical_issues_do_not_block_latest_pass(ready):
    app, identifier, _ = ready
    # Add a historical run with a lower ID than the actual successful run.
    with app.app_context():
        db.session.add(SubmissionJob(job_id=0, submission_id=identifier, requested_by_user_id=1,
            action="validate", idempotency_key="historical", status="succeeded", completed_at=datetime.now(timezone.utc)))
        db.session.flush()
        db.session.add(SubmissionIssue(submission_id=identifier, job_id=0, severity="error", error_code="OLD", message="History"))
        db.session.commit()
    assert review(client(app, 3), app, identifier).status_code == 303


def test_missing_worker_attestation_blocks_approval(ready):
    app, identifier, _ = ready
    with app.app_context():
        db.session.execute(delete(AuditLog).where(AuditLog.action == "submission_validation"))
        db.session.commit()
    assert review(client(app, 3), app, identifier).status_code == 400


def test_file_must_have_passed(ready):
    app, identifier, _ = ready
    with app.app_context():
        db.session.scalar(select(SubmissionFile)).validation_status = "pending"
        db.session.commit()
    assert review(client(app, 3), app, identifier).status_code == 400


def test_active_job_blocks_review(ready):
    app, identifier, _ = ready
    with app.app_context():
        db.session.add(SubmissionJob(submission_id=identifier, requested_by_user_id=3,
            action="cleanup", idempotency_key="active-cleanup"))
        db.session.commit()
    assert review(client(app, 3), app, identifier).status_code == 400


def test_rejection_note_required_and_visible_escaped_to_owner(ready):
    app, identifier, _ = ready
    c = client(app, 3)
    assert review(c, app, identifier, "reject", note="   ").status_code == 400
    assert review(c, app, identifier, "reject", note="<script>fix</script>").status_code == 303
    page = client(app, 1).get(f"/admin/challenge-submissions/{identifier}").data
    assert b"&lt;script&gt;fix&lt;/script&gt;" in page and b"<script>fix</script>" not in page
    with app.app_context():
        row = db.session.get(ChallengeSubmission, identifier)
        assert row.status == "rejected" and row.approved_at is None and row.approved_by_user_id is None
    assert post(client(app, 1), identifier, "revise").status_code == 303


def test_duplicate_decision_preserves_original_and_cannot_reverse_it(ready):
    app, identifier, _ = ready
    c = client(app, 3)
    assert review(c, app, identifier, note="Original note").status_code == 303
    assert review(c, app, identifier, note="Changed note").status_code == 303
    assert review(c, app, identifier, "reject", note="Reverse decision").status_code == 400
    with app.app_context():
        audits = list(db.session.scalars(select(AuditLog).where(AuditLog.action == "submission_review")))
        assert len(audits) == 1 and audits[0].details_json["note"] == "Original note"


def test_notification_failure_rolls_back_review(ready):
    app, identifier, _ = ready
    with app.app_context():
        engine = db.engines["pond"]
    def failure(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO notification_outbox"):
            raise RuntimeError("Simulated notification insert failure")
    event.listen(engine, "before_cursor_execute", failure)
    try:
        with pytest.raises(RuntimeError):
            review(client(app, 3), app, identifier)
    finally:
        event.remove(engine, "before_cursor_execute", failure)
    with app.app_context():
        row = db.session.get(ChallengeSubmission, identifier)
        assert row.status == "ready_for_review" and row.approved_at is None
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == "submission_review")) is None


@pytest.mark.parametrize("status", ["needs_changes", "validation_failed"])
def test_failed_validation_can_be_rejected_but_not_approved(ready, status):
    app, identifier, _ = ready
    with app.app_context():
        db.session.get(ChallengeSubmission, identifier).status = status
        db.session.commit()
    c = client(app, 3)
    assert review(c, app, identifier).status_code == 400
    assert review(c, app, identifier, "reject", note="Please correct the package.").status_code == 303


def test_concurrent_approval_creates_one_decision(ready):
    app, identifier, _ = ready
    with app.app_context():
        engine = db.engines["pond"]
        digest = db.session.get(ChallengeSubmission, identifier).content_digest
        job_id = db.session.scalar(select(SubmissionJob.job_id).where(SubmissionJob.submission_id == identifier))
    barrier = Barrier(2)
    def decide(_):
        barrier.wait()
        return ReviewService(engine).decide(identifier, actor_user_id=3, decision="approve",
                                            expected_digest=digest, expected_job_id=job_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(decide, [1, 2])) == [False, True]
