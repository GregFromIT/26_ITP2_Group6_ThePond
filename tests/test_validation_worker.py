"""Worker integration tests on temporary SQLite files; no real Proxmox/scans."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import io
from pathlib import Path
from threading import Barrier, Event

import pytest
from flask import Flask
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from db import (AuditLog, ChallengeSubmission, NotificationOutbox, Role, SubmissionFile, SubmissionIssue,
                SubmissionJob, User, db)
from challenge_ingestion import worker as module
from challenge_ingestion.storage import QuarantineStorage
from challenge_ingestion.validation import FileRecord, ValidationReport, content_digest
from challenge_ingestion.worker import LeaseLost, ValidationWorker


@pytest.fixture
def env(tmp_path):
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_BINDS={"pond": "sqlite:///" + str(tmp_path / "worker.db")})
    db.init_app(app)
    with app.app_context():
        db.create_all(bind_key="pond")
        engine = db.engines["pond"]
        manifest = json.loads((Path(__file__).resolve().parents[1] / "examples/challenge_uploads/vm-template.json").read_text())
        now = {"value": datetime(2026, 9, 28, 0, 0)}
        with Session(engine) as session:
            session.add(Role(role_id=1, role_name="sysadmin", role_level=3))
            session.flush()
            session.add(User(user_id=1, role_id=1, username="uploader", display_name="Uploader"))
            session.flush()
            session.add(ChallengeSubmission(submission_id=1, uploaded_by_user_id=1, title="Example",
                challenge_type="vm", schema_version=1, manifest_json=manifest,
                content_digest=content_digest(manifest, []), submitted_at=now["value"], status="validating"))
            session.flush()
            session.add(SubmissionJob(job_id=1, submission_id=1, requested_by_user_id=1, action="validate",
                idempotency_key="validate:1", available_at=now["value"]))
            session.commit()
        with QuarantineStorage(tmp_path / "storage") as storage:
            worker = ValidationWorker(engine, storage, worker_id="test-worker", clock=lambda: now["value"],
                                      lease_seconds=9, retry_seconds=10, verify_template=lambda vm: True)
            yield worker, engine, now
        db.session.remove()
        engine.dispose()


def state(engine):
    with Session(engine) as session:
        submission = session.get(ChallengeSubmission, 1)
        job = session.get(SubmissionJob, 1)
        return (submission.status, job.status, job.attempt_count,
                list(session.scalars(select(SubmissionIssue.error_code))),
                list(session.scalars(select(NotificationOutbox.event_type))))


def snapshot(worker, claim):
    with Session(worker.engine) as session:
        return worker._snapshot(session, session.get(ChallengeSubmission, claim.submission_id))[2]


def test_success_saves_result_audit_and_one_pending_notification(env):
    worker, engine, _ = env
    assert worker.run_once() == 1
    assert state(engine) == ("ready_for_review", "succeeded", 1, [], ["ready_for_review"])
    assert worker.run_once() is None
    with Session(engine) as session:
        notice = session.scalar(select(NotificationOutbox))
        assert notice.status == "pending" and notice.sent_at is None
        assert len(list(session.scalars(select(AuditLog)))) == 1


def test_missing_image_is_content_failure_not_retry(env):
    worker, engine, _ = env
    with Session(engine) as session:
        row = session.get(ChallengeSubmission, 1)
        manifest = deepcopy(row.manifest_json)
        manifest["vms"][0]["source"] = {"kind": "image", "path": "images/target.qcow2"}
        row.manifest_json = manifest
        row.content_digest = content_digest(manifest, [])
        session.commit()
    worker.run_once()
    assert state(engine) == ("needs_changes", "succeeded", 1, ["MISSING_IMAGE"], ["needs_changes"])


def test_unavailable_service_retries_then_fails_once(env):
    worker, engine, now = env
    worker.verify_template = None
    for attempt in range(1, 4):
        assert worker.run_once() == 1
        assert state(engine)[2] == attempt
        if attempt < 3:
            assert state(engine)[:2] == ("validating", "retry_wait")
            assert state(engine)[4] == []
            assert worker.run_once() is None
            now["value"] += timedelta(seconds=10)
    assert state(engine) == ("validation_failed", "failed", 3, ["CHECK_UNAVAILABLE"], ["validation_failed"])


def test_recovered_service_replaces_only_this_jobs_findings(env):
    worker, engine, now = env
    worker.verify_template = None
    worker.run_once()
    with Session(engine) as session:
        session.add(SubmissionJob(job_id=2, submission_id=1, requested_by_user_id=1, action="validate",
                                  idempotency_key="old-run", status="succeeded"))
        session.flush()
        session.add(SubmissionIssue(submission_id=1, job_id=2, severity="error", error_code="OLD", message="Historical"))
        session.commit()
    now["value"] += timedelta(seconds=10)
    worker.verify_template = lambda vm: True
    worker.run_once()
    assert state(engine)[3:] == (["OLD"], ["ready_for_review"])


def test_two_independent_workers_cannot_claim_same_job(env):
    worker, engine, now = env
    other = ValidationWorker(engine, worker.storage, worker_id="second", clock=lambda: now["value"])
    barrier = Barrier(2)
    def compete(w):
        barrier.wait()
        return w.claim()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, [worker, other]))
    assert sum(r is not None for r in results) == 1
    assert state(engine)[2] == 1


def test_expired_claim_replaced_and_stale_finish_rejected(env):
    worker, engine, now = env
    old = worker.claim()
    identity = snapshot(worker, old)
    now["value"] += timedelta(seconds=10)
    new = worker.claim()
    assert new.token != old.token and state(engine)[2] == 2
    with pytest.raises(LeaseLost):
        worker.finish(old, ValidationReport(()), identity)
    assert state(engine)[4] == []
    worker.finish(new, ValidationReport(()), snapshot(worker, new))
    assert state(engine)[0] == "ready_for_review"


def test_expired_last_attempt_fails_without_another_validation(env):
    worker, engine, now = env
    with Session(engine) as session:
        session.get(SubmissionJob, 1).max_attempts = 1
        session.commit()
    worker.claim()
    now["value"] += timedelta(seconds=10)
    assert worker.claim() is None
    assert state(engine) == ("validation_failed", "failed", 1, ["CHECK_UNAVAILABLE"], ["validation_failed"])


def test_renewal_extends_lease_but_cannot_resurrect_expired_claim(env):
    worker, _, now = env
    claim = worker.claim()
    now["value"] += timedelta(seconds=8)
    worker.renew(claim)
    now["value"] += timedelta(seconds=8)
    worker.renew(claim)
    now["value"] += timedelta(seconds=10)
    with pytest.raises(LeaseLost):
        worker.renew(claim)


def test_changed_content_cannot_receive_stale_pass(env):
    worker, engine, _ = env
    claim = worker.claim()
    identity = snapshot(worker, claim)
    with Session(engine) as session:
        row = session.get(ChallengeSubmission, 1)
        row.manifest_json = {**row.manifest_json, "title": "Changed"}
        session.commit()
    worker.finish(claim, ValidationReport(()), identity)
    assert state(engine)[0] == "needs_changes" and state(engine)[3] == ["CONTENT_CHANGED"]


def test_atomic_rollback_when_notification_insert_fails(env):
    worker, engine, _ = env
    def reject_notification(conn, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO notification_outbox"):
            raise RuntimeError("Simulated database failure")
    event.listen(engine, "before_cursor_execute", reject_notification)
    try:
        with pytest.raises(RuntimeError, match="Simulated database failure"):
            worker.run_once()
    finally:
        event.remove(engine, "before_cursor_execute", reject_notification)
    assert state(engine) == ("validating", "running", 1, [], [])
    with Session(engine) as session:
        assert session.scalar(select(AuditLog)) is None


def test_unexpected_validator_failure_is_retried_without_leaking_error(env, monkeypatch):
    worker, engine, _ = env
    def failure(*args, **kwargs):
        raise RuntimeError("secret diagnostic")
    monkeypatch.setattr(module, "validate_submission", failure)
    worker.run_once()
    assert state(engine)[:2] == ("validating", "retry_wait")
    with Session(engine) as session:
        assert "secret diagnostic" not in session.scalar(select(SubmissionIssue.message))


def test_nonvalidation_jobs_untouched(env):
    worker, engine, _ = env
    with Session(engine) as session:
        session.get(SubmissionJob, 1).action = "publish"
        session.commit()
    assert worker.run_once() is None
    assert state(engine)[1:3] == ("queued", 0)


def test_wrong_submission_state_cancels_job_without_changing_submission(env):
    worker, engine, _ = env
    with Session(engine) as session:
        session.get(ChallengeSubmission, 1).status = "needs_changes"
        session.commit()
    assert worker.claim() is None
    assert state(engine)[:2] == ("needs_changes", "cancelled")


def test_finished_claim_cannot_finish_twice(env):
    worker, engine, _ = env
    claim = worker.claim()
    identity = snapshot(worker, claim)
    worker.finish(claim, ValidationReport(()), identity)
    with pytest.raises(LeaseLost):
        worker.finish(claim, ValidationReport(()), identity)
    assert state(engine)[4] == ["ready_for_review"]


def test_background_heartbeat_runs_during_verification(env, monkeypatch):
    worker, engine, _ = env
    worker.lease_seconds = 3
    renewed = Event()
    original = worker.renew
    def renew(claim):
        original(claim)
        renewed.set()
    monkeypatch.setattr(worker, "renew", renew)
    worker.verify_template = lambda vm: renewed.wait(timeout=3)
    worker.run_once()
    assert renewed.is_set() and state(engine)[0] == "ready_for_review"


def test_completed_image_validation_updates_file_record(env):
    worker, engine, _ = env
    stored = worker.storage.save(io.BytesIO(b"QFI\xfb" + b"dummy disk"), remaining_bytes=100)
    record = FileRecord(1, 1, "images/target.qcow2", "image", stored.storage_key, stored.size_bytes, stored.sha256)
    with Session(engine) as session:
        submission = session.get(ChallengeSubmission, 1)
        manifest = deepcopy(submission.manifest_json)
        manifest["vms"][0]["source"] = {"kind": "image", "path": record.logical_path}
        submission.manifest_json = manifest
        submission.content_digest = content_digest(manifest, [record])
        session.add(SubmissionFile(**vars(record), original_filename="target.qcow2"))
        session.commit()
    worker.inspect_image = lambda handle, vms: True  # test-only fake inspection
    worker.run_once()
    with Session(engine) as session:
        row = session.get(SubmissionFile, 1)
        assert row.validation_status == "passed" and row.validated_at is not None
