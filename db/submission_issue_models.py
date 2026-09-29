"""Validation findings retained with the submission revision that produced them.

Missing-file findings have no file_id. Composite foreign keys ensure linked
files/jobs belong to the same submission. The application decides whether a
finding blocks publication and whether a correction has actually resolved it.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.orm import db


def utc_now():
    return datetime.now(timezone.utc)


class SubmissionIssue(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "submission_issues"
    __table_args__ = (
        CheckConstraint("severity IN ('error', 'warning', 'info')", name="ck_submission_issue_severity"),
        ForeignKeyConstraint(
            ["submission_id", "file_id"],
            ["submission_files.submission_id", "submission_files.file_id"],
            ondelete="RESTRICT", name="fk_submission_issue_file",
        ),
        ForeignKeyConstraint(
            ["submission_id", "job_id"],
            ["submission_jobs.submission_id", "submission_jobs.job_id"],
            ondelete="RESTRICT", name="fk_submission_issue_job",
        ),
        Index("ix_submission_issue_lookup", "submission_id", "job_id", "severity"),
    )

    issue_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("challenge_submissions.submission_id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    file_id: Mapped[Optional[int]] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    error_code: Mapped[str] = mapped_column(String(80), nullable=False)
    field_path: Mapped[Optional[str]] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text)
    resolved_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    submission: Mapped["ChallengeSubmission"] = relationship(foreign_keys=[submission_id])
    resolved_by: Mapped[Optional["User"]] = relationship()
    # Read-only navigation avoids conflicting ORM writes to submission_id from
    # three relationships. Create findings with explicit submission/job/file IDs.
    # Expire/refresh an already loaded relationship after changing those IDs.
    job: Mapped["SubmissionJob"] = relationship(viewonly=True)
    file: Mapped[Optional["SubmissionFile"]] = relationship(viewonly=True)
