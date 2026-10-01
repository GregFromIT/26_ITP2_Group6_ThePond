"""Atomic in-app delivery and recipient-scoped inbox operations.

No email, network sends or browser routes. The web layer must pass the user ID
from its authenticated session, never from request parameters, and enforce CSRF
on read acknowledgements. This service uses independent short transactions.
"""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from db import NotificationOutbox, User, db


@dataclass(frozen=True)
class InboxItem:
    notification_id: int
    submission_id: int
    event_type: str
    message: str
    sent_at: datetime
    read_at: datetime | None


@dataclass(frozen=True)
class DeliveryResult:
    sent: int
    failed: int


def _positive(value, name, maximum=None):
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ValueError(f"Invalid {name}")
    return value


class NotificationService:
    def __init__(self, engine, *, max_attempts=3, clock=None):
        if engine.dialect.name != "sqlite":
            raise ValueError("This notification service currently requires SQLite")
        self.engine = engine
        self.max_attempts = _positive(max_attempts, "max_attempts")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self):
        value = self.clock()
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value

    @contextmanager
    def _write(self):
        with Session(self.engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def deliver_pending(self, *, limit=100):
        """Make a bounded batch available atomically, without external sends.

        No intermediate claim is needed for local in-app delivery: visibility
        is the committed status='sent' update. Existing unexpired delivery
        leases are respected; expired ones are recoverable. Failed rows require
        explicit operator review/requeue and are not automatically retried.
        """
        _positive(limit, "limit", maximum=1000)
        sent = failed = 0
        with self._write() as session:
            now = self._now()
            rows = list(session.scalars(select(NotificationOutbox).where(
                NotificationOutbox.channel == "in_app",
                or_(and_(NotificationOutbox.status == "pending", NotificationOutbox.available_at <= now),
                    and_(NotificationOutbox.status == "delivering", NotificationOutbox.lease_expires_at <= now)),
            ).order_by(NotificationOutbox.available_at, NotificationOutbox.notification_id).limit(limit)))
            for row in rows:
                recipient = session.get(User, row.recipient_user_id)
                if row.attempt_count >= self.max_attempts:
                    row.status = "failed"
                    row.last_error = "Delivery attempt limit reached; operator review required."
                    failed += 1
                elif recipient is None or not recipient.is_active:
                    row.attempt_count += 1
                    row.status = "failed"
                    row.last_error = "Recipient is unavailable; operator review required."
                    failed += 1
                else:
                    row.attempt_count += 1
                    row.status = "sent"
                    row.sent_at = now
                    row.read_at = None
                    row.last_error = None
                    sent += 1
                row.lease_token = None
                row.lease_expires_at = None
        return DeliveryResult(sent, failed)

    @staticmethod
    def _actor(session, actor_user_id):
        _positive(actor_user_id, "authenticated user ID")
        user = session.get(User, actor_user_id)
        if user is None or not user.is_active:
            raise PermissionError("Inbox access denied")

    def inbox(self, *, actor_user_id, limit=50, before_id=None, unread_only=False):
        """Return safe fields for the trusted authenticated actor, newest ID first.

        Use the last item's notification_id as before_id for the next page.
        Refresh from the first page to see notifications delivered later with
        older IDs. Rendering must escape all text and authorize submission links.
        """
        _positive(limit, "limit", maximum=100)
        if before_id is not None:
            _positive(before_id, "before_id")
        if type(unread_only) is not bool:
            raise ValueError("unread_only must be boolean")
        with Session(self.engine) as session:
            self._actor(session, actor_user_id)
            query = select(NotificationOutbox).where(
                NotificationOutbox.recipient_user_id == actor_user_id,
                NotificationOutbox.channel == "in_app", NotificationOutbox.status == "sent")
            if before_id is not None:
                query = query.where(NotificationOutbox.notification_id < before_id)
            if unread_only:
                query = query.where(NotificationOutbox.read_at.is_(None))
            rows = session.scalars(query.order_by(NotificationOutbox.notification_id.desc()).limit(limit))
            return tuple(InboxItem(row.notification_id, row.submission_id, row.event_type,
                                   row.message, row.sent_at, row.read_at) for row in rows)

    def unread_count(self, *, actor_user_id):
        with Session(self.engine) as session:
            self._actor(session, actor_user_id)
            return session.scalar(select(func.count()).select_from(NotificationOutbox).where(
                NotificationOutbox.recipient_user_id == actor_user_id,
                NotificationOutbox.channel == "in_app", NotificationOutbox.status == "sent",
                NotificationOutbox.read_at.is_(None)))

    def mark_read(self, notification_id, *, actor_user_id):
        """Idempotent acknowledgement; False for absent/foreign/unsent records.

        Only updates read_at. Does not resolve submission issues or publish work.
        Missing and other users' IDs deliberately give the same response.
        """
        _positive(notification_id, "notification_id")
        with self._write() as session:
            self._actor(session, actor_user_id)
            row = session.scalar(select(NotificationOutbox).where(
                NotificationOutbox.notification_id == notification_id,
                NotificationOutbox.recipient_user_id == actor_user_id,
                NotificationOutbox.channel == "in_app", NotificationOutbox.status == "sent"))
            if row is None:
                return False
            if row.read_at is None:
                row.read_at = self._now()
            return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deliver one bounded batch of in-app notifications.")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pond-sec"))
    from app import create_app
    app = create_app()
    with app.app_context():
        result = NotificationService(db.engines["pond"]).deliver_pending(limit=args.limit)
        print(f"In-app notifications delivered: {result.sent}; failed: {result.failed}.")


if __name__ == "__main__":
    main()
