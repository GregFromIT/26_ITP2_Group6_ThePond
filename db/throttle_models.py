"""
Rate limit config for key actions eg log in, submit flag....
"""

from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from db.orm import db

def utc_now():
    return datetime.now(timezone.utc)


class ThrottleEvent(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "throttle_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )