"""Lease-based validation worker. Publishing/cleanup jobs are left untouched.

Uses its own sessions and short SQLite write transactions. Do not call with an
uncommitted upload transaction. Callers must enqueue authorized frozen revisions
with submission status 'validating'; this module provides no upload endpoint.
"""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import secrets
import sys
from pathlib import Path
from threading import Event, Thread

from sqlalchemy import delete, or_, and_, select, text
from sqlalchemy.orm import Session

from db import (AuditLog, ChallengeSubmission, NotificationOutbox, SubmissionFile,
                SubmissionIssue, SubmissionJob, db)
from .storage import QuarantineStorage
from .validation import FileRecord, Finding, ValidationReport, validate_submission


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class Claim:
    job_id: int
    submission_id: int
    token: str


class ValidationWorker:
    def __init__(self, engine, storage, *, worker_id, verify_template=None,
                 inspect_image=None, lease_seconds=120, retry_seconds=60, clock=None):
        if engine.dialect.name != "sqlite":
            raise ValueError("This worker currently requires SQLite")
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 100:
            raise ValueError("worker_id must contain 1–100 characters")
        if type(lease_seconds) is not int or lease_seconds < 3:
            raise ValueError("lease_seconds must be an integer >= 3")
        if type(retry_seconds) is not int or retry_seconds < 0:
            raise ValueError("retry_seconds must be a non-negative integer")
        self.engine, self.storage, self.worker_id = engine, storage, worker_id
        self.verify_template, self.inspect_image = verify_template, inspect_image
        self.lease_seconds, self.retry_seconds = lease_seconds, retry_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self):
        value = self.clock()
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value

    @contextmanager
    def _write(self):
        with Session(self.engine) as session:
            # Acquire the SQLite writer lock before reading eligibility. This
            # avoids a select-then-update race between independent workers.
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def _owned(self, session, claim):
        job = session.get(SubmissionJob, claim.job_id)
        if (job is None or job.action != "validate" or job.status != "running"
                or job.submission_id != claim.submission_id or job.lease_token != claim.token
                or job.lease_expires_at is None or job.lease_expires_at <= self._now()):
            raise LeaseLost("Validation claim expired or was replaced")
        return job

    def _notify(self, session, job, submission, status):
        key = f"validation-job:{job.job_id}:terminal:user:{submission.uploaded_by_user_id}:in_app"
        existing = session.scalar(select(NotificationOutbox.notification_id).where(
            NotificationOutbox.deduplication_key == key))
        if existing is None:
            messages = {
                "ready_for_review": "Your challenge submission passed validation and is ready for review.",
                "needs_changes": "Your challenge submission needs changes. Open its validation issues for details.",
                "validation_failed": "Validation could not finish. A system administrator needs to review the failed job.",
            }
            session.add(NotificationOutbox(submission_id=submission.submission_id,
                recipient_user_id=submission.uploaded_by_user_id, event_type=status,
                deduplication_key=key, message=messages[status]))

    def _result(self, session, job, submission, report, *, exhausted=False):
        now = self._now()
        technical = report.suggested_status == "validation_failed"
        retry = technical and not exhausted and job.attempt_count < job.max_attempts
        # Replace only findings for this retried job; preserve other run history.
        session.execute(delete(SubmissionIssue).where(SubmissionIssue.job_id == job.job_id))
        for finding in report.findings:
            session.add(SubmissionIssue(submission_id=submission.submission_id, job_id=job.job_id,
                file_id=finding.file_id, severity=finding.severity, error_code=finding.error_code,
                message=finding.message, field_path=finding.field_path))
        submission.status = "validating" if retry else report.suggested_status
        submission.updated_at = now
        job.status = "retry_wait" if retry else ("failed" if technical else "succeeded")
        job.available_at = now + timedelta(seconds=self.retry_seconds)
        job.completed_at = None if retry else now
        job.error_code = "VALIDATION_INFRASTRUCTURE" if technical else None
        job.error_message = "Required validation checks could not complete." if technical else None
        job.locked_by = job.lease_token = None
        job.lease_expires_at = None
        # Failed reports may have returned before checking files. Never infer a
        # passed file from absence of file-specific errors on such a report.
        failed_ids = {f.file_id for f in report.findings if f.file_id is not None
                      and f.error_code not in {"CHECK_UNAVAILABLE", "STORAGE_UNAVAILABLE"}}
        for record in session.scalars(select(SubmissionFile).where(SubmissionFile.submission_id == submission.submission_id)):
            record.validation_status = "passed" if report.passed else ("failed" if record.file_id in failed_ids else "pending")
            record.validated_at = now if record.validation_status != "pending" else None
        session.add(AuditLog(actor_user_id=None, action="submission_validation", target_type="submission",
            target_id=submission.submission_id, details_json={"job_id": job.job_id,
            "attempt": job.attempt_count, "status": submission.status, "issue_count": len(report.findings),
            "content_digest": submission.content_digest if report.passed else None}))
        if not retry:
            self._notify(session, job, submission, submission.status)

    def claim(self):
        """Atomically claim one eligible job or recover expired validation work."""
        with self._write() as session:
            now = self._now()
            while True:
                job = session.scalar(select(SubmissionJob).where(
                    SubmissionJob.action == "validate",
                    or_(and_(SubmissionJob.status.in_(("queued", "retry_wait")), SubmissionJob.available_at <= now),
                        and_(SubmissionJob.status == "running", SubmissionJob.lease_expires_at <= now)),
                ).order_by(SubmissionJob.available_at, SubmissionJob.job_id).limit(1))
                if job is None:
                    return None
                submission = session.get(ChallengeSubmission, job.submission_id)
                if submission.status != "validating":
                    job.status = "cancelled"
                    job.completed_at = now
                    job.error_code = "SUBMISSION_NOT_VALIDATING"
                    job.error_message = "Submission is not in the validation workflow state."
                    job.locked_by = job.lease_token = job.lease_expires_at = None
                    session.flush()
                    continue
                if job.attempt_count >= job.max_attempts:
                    report = ValidationReport((Finding("CHECK_UNAVAILABLE", "Validation attempt limit was reached; administrator review is required."),))
                    self._result(session, job, submission, report, exhausted=True)
                    session.flush()
                    continue
                token = secrets.token_hex(32)
                job.status = "running"
                job.attempt_count += 1
                job.locked_by, job.lease_token = self.worker_id, token
                job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                job.heartbeat_at = now
                job.started_at = job.started_at or now
                job.completed_at = None
                return Claim(job.job_id, job.submission_id, token)

    def renew(self, claim):
        with self._write() as session:
            job = self._owned(session, claim)
            job.heartbeat_at = self._now()
            job.lease_expires_at = job.heartbeat_at + timedelta(seconds=self.lease_seconds)

    def _snapshot(self, session, submission):
        files = tuple(FileRecord(row.file_id, row.submission_id, row.logical_path, row.file_role,
                      row.storage_key, row.size_bytes, row.sha256) for row in session.scalars(
                      select(SubmissionFile).where(SubmissionFile.submission_id == submission.submission_id)
                      .order_by(SubmissionFile.file_id)))
        manifest_bytes = json.dumps(submission.manifest_json, ensure_ascii=True, allow_nan=False).encode()
        identity = (submission.content_digest, submission.challenge_type, submission.schema_version,
                    submission.uploaded_by_user_id, manifest_bytes, files)
        return files, manifest_bytes, identity

    def finish(self, claim, report, snapshot_identity):
        """Commit findings/status/outbox atomically only for the current claim."""
        with self._write() as session:
            job = self._owned(session, claim)
            submission = session.get(ChallengeSubmission, job.submission_id)
            if submission.status != "validating":
                raise LeaseLost("Submission left the validation workflow")
            _, _, current = self._snapshot(session, submission)
            if current != snapshot_identity:
                report = ValidationReport((Finding("CONTENT_CHANGED", "Submission changed during validation; submit a corrected revision."),))
            self._result(session, job, submission, report)

    def run_once(self):
        """Return a claimed job ID, or None if no eligible work; no daemon loop."""
        claim = self.claim()
        if claim is None:
            return None
        stop, lost = Event(), Event()
        def heartbeat():
            while not stop.wait(self.lease_seconds / 3):
                try:
                    self.renew(claim)
                except Exception:
                    lost.set()
                    return
        thread = Thread(target=heartbeat, name="validation-lease", daemon=True)
        thread.start()
        try:
            with Session(self.engine) as session:
                self._owned(session, claim)
                submission = session.get(ChallengeSubmission, claim.submission_id)
                files, manifest_bytes, identity = self._snapshot(session, submission)
                args = dict(submission_id=submission.submission_id, challenge_type=submission.challenge_type,
                            schema_version=submission.schema_version, expected_digest=submission.content_digest)
            try:
                report = validate_submission(manifest_bytes, files, storage=self.storage,
                    verify_template=self.verify_template, inspect_image=self.inspect_image, **args)
            except Exception:
                report = ValidationReport((Finding("CHECK_UNAVAILABLE", "Validation failed unexpectedly; administrator review is required."),))
            if lost.is_set():
                raise LeaseLost("Worker could not maintain its claim")
            self.finish(claim, report, identity)
        finally:
            stop.set()
            thread.join(timeout=self.lease_seconds)
        return claim.job_id


def main(argv=None):
    parser = argparse.ArgumentParser(description="Process one queued validation job using the web app configuration.")
    parser.add_argument("--storage-root", required=True, help="Absolute private quarantine directory")
    parser.add_argument("--worker-id", required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pond-sec"))
    from app import create_app
    app = create_app()
    with app.app_context(), QuarantineStorage(args.storage_root) as storage:
        worker = ValidationWorker(db.engines["pond"], storage, worker_id=args.worker_id,
            verify_template=app.config.get("INGESTION_VERIFY_TEMPLATE"),
            inspect_image=app.config.get("INGESTION_INSPECT_IMAGE"))
        result = worker.run_once()
        print("No eligible validation job." if result is None else f"Processed validation job {result}.")


if __name__ == "__main__":
    main()
