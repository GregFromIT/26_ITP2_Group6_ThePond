"""Durable in-app notifications for challenge submission events.

Create outbox records in the same transaction as the event they describe.
Delivery, authorization, retry limits and read acknowledgement belong to the
application service. This model neither sends messages nor contacts users.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.orm import db


def utc_now():
    return datetime.now(timezone.utc)


class NotificationOutbox(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "notification_outbox"
    __table_args__ = (
        CheckConstraint("channel = 'in_app'", name="ck_notification_channel"),
        CheckConstraint(
            "status IN ('pending', 'delivering', 'sent', 'failed')",
            name="ck_notification_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_notification_attempts"),
        CheckConstraint(
            "status <> 'delivering' OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_notification_delivery_lease",
        ),
        CheckConstraint("status <> 'sent' OR sent_at IS NOT NULL", name="ck_notification_sent_at"),
        Index("ix_notification_queue", "status", "available_at"),
        Index("ix_notification_recipient", "recipient_user_id", "read_at"),
    )

    notification_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("challenge_submissions.submission_id", ondelete="RESTRICT"), nullable=False
    )
    recipient_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Server-generated identity for event occurrence + recipient + channel.
    # Retries reuse this key; a genuinely new event receives a new one.
    deduplication_key: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    channel: Mapped[str] = mapped_column(
        String(20), nullable=False, default="in_app", server_default=text("'in_app'")
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    lease_token: Mapped[Optional[str]] = mapped_column(String(100))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )

    submission: Mapped["ChallengeSubmission"] = relationship()
    recipient: Mapped["User"] = relationship()
