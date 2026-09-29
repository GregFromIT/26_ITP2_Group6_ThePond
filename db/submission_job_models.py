"""Durable work records for challenge ingestion, not runtime instance jobs.

The future worker must atomically claim jobs, enforce lease ownership and retry
limits, and reconcile Proxmox operations after crashes. This model runs no work.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.orm import db


def utc_now():
    return datetime.now(timezone.utc)


class SubmissionJob(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "submission_jobs"
    __table_args__ = (
        CheckConstraint("action IN ('validate', 'publish', 'cleanup')", name="ck_submission_job_action"),
        CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')",
            name="ck_submission_job_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_submission_job_attempts"),
        CheckConstraint("max_attempts >= 1", name="ck_submission_job_max_attempts"),
        CheckConstraint(
            "json_valid(resource_inventory_json) AND json_type(resource_inventory_json) = 'array'",
            name="ck_submission_job_inventory",
        ),
        CheckConstraint(
            "status <> 'running' OR (locked_by IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_submission_job_running_lease",
        ),
        UniqueConstraint("submission_id", "job_id", name="uq_submission_job_identity"),
        Index("ix_submission_job_queue", "status", "available_at"),
        Index(
            "uq_submission_active_job", "submission_id", unique=True,
            sqlite_where=text("status IN ('queued', 'running', 'retry_wait')"),
        ),
    )

    job_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("challenge_submissions.submission_id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued", server_default=text("'queued'")
    )
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default=text("3"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    locked_by: Mapped[Optional[str]] = mapped_column(String(100))
    lease_token: Mapped[Optional[str]] = mapped_column(String(100))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Replace the whole list when updating; nested JSON edits are not tracked.
    # Record owned resource reservations before external work, then task/results.
    resource_inventory_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(80))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    submission: Mapped["ChallengeSubmission"] = relationship()
    requested_by: Mapped["User"] = relationship()
