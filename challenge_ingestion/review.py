"""Atomic sysadmin review of frozen challenge revisions; no publication."""

from datetime import datetime, timezone
import hmac
import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db import (AuditLog, ChallengeSubmission, NotificationOutbox, SubmissionFile,
                SubmissionIssue, SubmissionJob, User)
from .validation import FileRecord, content_digest


class ReviewError(ValueError):
    pass


class ReviewService:
    def __init__(self, engine):
        if engine.dialect.name != "sqlite":
            raise ValueError("Review currently requires SQLite")
        self.engine = engine

    def decide(self, submission_id, *, actor_user_id, decision, expected_digest, expected_job_id, note=""):
        """Review with identity from authentication and snapshot from the page.

        Digest/job fields detect stale forms; they never authorize a decision.
        Approval checks a successful worker attestation. Rejection is allowed
        after a terminal validation run, even when content failed validation.
        """
        if decision not in {"approve", "reject"}:
            raise ReviewError("Unknown review decision.")
        if type(expected_job_id) is not int or expected_job_id <= 0:
            raise ReviewError("Refresh the page before reviewing this submission.")
        if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            raise ReviewError("Refresh the page before reviewing this submission.")
        if not isinstance(note, str) or len(note) > 2000:
            raise ReviewError("Review notes must be at most 2000 characters.")
        note = note.strip()
        if any(ord(c) < 32 and c not in "\n\r\t" for c in note):
            raise ReviewError("Review note contains unsupported characters.")
        if decision == "reject" and not note:
            raise ReviewError("Explain what needs to change before rejecting the submission.")
        with Session(self.engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            actor = session.get(User, actor_user_id)
            if actor is None or not actor.is_active or actor.role.role_name != "sysadmin":
                raise PermissionError("System administrator review is required")
            submission = session.get(ChallengeSubmission, submission_id)
            if submission is None:
                raise LookupError("Submission not found")
            if not submission.content_digest or not hmac.compare_digest(submission.content_digest, expected_digest):
                raise ReviewError("The submission changed. Refresh and review the current revision.")
            job = session.scalar(select(SubmissionJob).where(SubmissionJob.submission_id == submission_id,
                SubmissionJob.action == "validate").order_by(SubmissionJob.job_id.desc()).limit(1))
            if job is None or job.job_id != expected_job_id:
                raise ReviewError("The validation run changed. Refresh before reviewing.")
            if session.scalar(select(SubmissionJob.job_id).where(SubmissionJob.submission_id == submission_id,
                    SubmissionJob.status.in_(("queued", "running", "retry_wait"))).limit(1)):
                raise ReviewError("Wait for active processing to finish before reviewing.")
            files = list(session.scalars(select(SubmissionFile).where(SubmissionFile.submission_id == submission_id)))
            inventory = tuple(FileRecord(f.file_id, f.submission_id, f.logical_path, f.file_role,
                                         f.storage_key, f.size_bytes, f.sha256) for f in files)
            if (not isinstance(submission.manifest_json, dict)
                    or submission.challenge_type != submission.manifest_json.get("challenge_type")
                    or submission.schema_version != submission.manifest_json.get("schema_version")):
                raise ReviewError("Submission metadata differs from its manifest. Revalidation is required.")
            try:
                actual_digest = content_digest(submission.manifest_json, inventory)
            except (ValueError, TypeError):
                raise ReviewError("Stored metadata is invalid. Submit a corrected revision.") from None
            if not hmac.compare_digest(actual_digest, expected_digest):
                raise ReviewError("Metadata or file inventory changed after submission. Revalidation is required.")

            final_status = "approved" if decision == "approve" else "rejected"
            # A repeated click returns the original decision without changing
            # its author/note/time or inserting a second notification.
            if submission.status == final_status:
                session.rollback()
                return False
            if submission.status not in {"ready_for_review", "needs_changes", "validation_failed"}:
                raise ReviewError("This submission is not awaiting a review decision.")
            if job.status not in {"succeeded", "failed"} or job.completed_at is None:
                raise ReviewError("A completed validation run is required.")
            if decision == "approve":
                if submission.status != "ready_for_review" or job.status != "succeeded":
                    raise ReviewError("Only a submission that passed validation can be approved.")
                if session.scalar(select(SubmissionIssue.issue_id).where(SubmissionIssue.job_id == job.job_id).limit(1)):
                    raise ReviewError("The latest validation run has findings. Revalidation is required.")
                if any(f.validation_status != "passed" or f.validated_at is None for f in files):
                    raise ReviewError("Every uploaded file must pass validation before approval.")
                attestation = session.scalar(select(AuditLog).where(AuditLog.action == "submission_validation",
                    AuditLog.target_type == "submission", AuditLog.target_id == submission_id)
                    .order_by(AuditLog.audit_id.desc()).limit(1))
                evidence = attestation.details_json if attestation else None
                if (not isinstance(evidence, dict) or evidence.get("job_id") != job.job_id
                        or evidence.get("status") != "ready_for_review"
                        or evidence.get("content_digest") != expected_digest or evidence.get("issue_count") != 0):
                    raise ReviewError("A successful validation record for this exact revision is required. Revalidate before approval.")
            now = datetime.now(timezone.utc)
            submission.status = final_status
            submission.approved_by_user_id = actor.user_id if decision == "approve" else None
            submission.approved_at = now if decision == "approve" else None
            submission.updated_at = now
            session.add(AuditLog(actor_user_id=actor.user_id, action="submission_review", target_type="submission",
                target_id=submission_id, details_json={"decision": decision, "note": note,
                "content_digest": expected_digest, "job_id": job.job_id}))
            message = ("Your submission was approved. Publication is still pending." if decision == "approve"
                       else "Your submission was rejected. Open it to read the review and create a correction.")
            session.add(NotificationOutbox(submission_id=submission_id, recipient_user_id=submission.uploaded_by_user_id,
                event_type=final_status, deduplication_key=f"review:{submission_id}:{expected_digest}:{decision}", message=message))
            session.commit()
            return True
