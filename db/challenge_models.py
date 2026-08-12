"""
db/challenge_models.py

Challenge models for The Pond database.

Relationships:

users
  1
  |
  N
challenges
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.orm import db


def utc_now():
    return datetime.now(timezone.utc)


class Challenge(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "challenges"

    challenge_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    title: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    instructions: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    category: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True
    )

    difficulty: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft"
    )

    time_limit_minutes: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )

    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "users.user_id",
            ondelete="SET NULL"
        ),
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now
    )

    created_by: Mapped[Optional["User"]] = relationship()

    vm_templates: Mapped[list["VMTemplate"]] = relationship(
        back_populates="challenge",
        cascade="all, delete-orphan"
    )
