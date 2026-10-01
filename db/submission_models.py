"""Staged challenge submissions, separate from live challenges.

Submitted revisions and their files must be frozen by the ingestion service.
These constraints enforce record consistency, not authorization, validation,
state transitions or the presence of required files. Nothing is published or
deleted by this model. Retain failed submissions for correction and review.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.orm import db


def utc_now():
    return datetime.now(timezone.utc)


SUBMISSION_STATUSES = (
    "draft", "validating", "validation_failed", "needs_changes",
    "ready_for_review", "approved", "importing", "import_failed",
    "published", "rejected",
)


class ChallengeSubmission(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "challenge_submissions"
    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="ck_submission_schema_version"),
        CheckConstraint(
            "json_valid(manifest_json) AND json_type(manifest_json) = 'object'",
            name="ck_submission_manifest_object",
        ),
        CheckConstraint(
            "status IN (" + ", ".join(repr(value) for value in SUBMISSION_STATUSES) + ")",
            name="ck_submission_status",
        ),
        CheckConstraint(
            "content_digest IS NULL OR (length(content_digest) = 64 "
            "AND content_digest NOT GLOB '*[^0-9a-f]*')",
            name="ck_submission_digest",
        ),
        CheckConstraint(
            "supersedes_submission_id IS NULL OR supersedes_submission_id <> submission_id",
            name="ck_submission_not_self_revision",
        ),
        CheckConstraint(
            "(approved_by_user_id IS NULL AND approved_at IS NULL) OR "
            "(approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_submission_approval_pair",
        ),
        CheckConstraint(
            "status = 'draft' OR (content_digest IS NOT NULL AND submitted_at IS NOT NULL)",
            name="ck_submission_finalized",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'importing', 'import_failed', 'published') OR "
            "(approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_submission_requires_approval",
        ),
        CheckConstraint(
            "(status = 'published' AND published_challenge_id IS NOT NULL AND published_at IS NOT NULL) OR "
            "(status <> 'published' AND published_challenge_id IS NULL AND published_at IS NULL)",
            name="ck_submission_publication_pair",
        ),
        Index("ix_submission_uploader", "uploaded_by_user_id", "created_at"),
        Index("ix_submission_status", "status", "created_at"),
        Index("ix_submission_previous", "supersedes_submission_id"),
    )

    submission_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uploaded_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    challenge_type: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    # Assign a new dictionary when editing a draft; nested in-place JSON edits
    # are not automatically tracked by SQLAlchemy. Never store VM bytes here.
    manifest_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="draft", server_default=text("'draft'")
    )
    supersedes_submission_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("challenge_submissions.submission_id", ondelete="RESTRICT")
    )
    content_digest: Mapped[Optional[str]] = mapped_column(String(64))
    approved_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT")
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_challenge_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("challenges.challenge_id", ondelete="RESTRICT"), unique=True
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Explicit foreign_keys disambiguates uploader from approver.
    uploaded_by: Mapped["User"] = relationship(foreign_keys=[uploaded_by_user_id])
    approved_by: Mapped[Optional["User"]] = relationship(foreign_keys=[approved_by_user_id])
    published_challenge: Mapped[Optional["Challenge"]] = relationship()
    supersedes: Mapped[Optional["ChallengeSubmission"]] = relationship(
        remote_side=[submission_id], foreign_keys=[supersedes_submission_id]
    )
