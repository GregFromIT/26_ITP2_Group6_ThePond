"""
db/scoring_models.py

Scoring models for The Pond database.

These tables track:
- every flag submission attempt
- successful solves and awarded points

Relationships:

users
  |
  +---- flag_submissions
  |
  +---- user_solves

challenge_instances
  |
  +---- flag_submissions
  |
  +---- user_solves

vm_instances
  |
  +---- flag_submissions

challenge_flags
  |
  +---- flag_submissions
  |
  +---- user_solves
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.orm import db


def utc_now():
    return datetime.now(timezone.utc)


class FlagSubmission(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "flag_submissions"

    submission_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.user_id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    instance_id: Mapped[int] = mapped_column(
        ForeignKey(
            "challenge_instances.instance_id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    vm_instance_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "vm_instances.vm_instance_id",
            ondelete="SET NULL"
        ),
        nullable=True
    )

    matched_flag_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "challenge_flags.flag_id",
            ondelete="SET NULL"
        ),
        nullable=True
    )

    was_correct: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )

    was_already_solved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )

    scoring_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )

    user: Mapped["User"] = relationship()

    challenge_instance: Mapped["ChallengeInstance"] = relationship()

    vm_instance: Mapped[Optional["VMInstance"]] = relationship()

    matched_flag: Mapped[Optional["ChallengeFlag"]] = relationship()


class UserSolve(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "user_solves"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "flag_id",
            name="uq_user_flag_solve"
        ),
    )

    solve_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.user_id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    flag_id: Mapped[int] = mapped_column(
        ForeignKey(
            "challenge_flags.flag_id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    instance_id: Mapped[int] = mapped_column(
        ForeignKey(
            "challenge_instances.instance_id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    awarded_points: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    solved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )

    user: Mapped["User"] = relationship()

    flag: Mapped["ChallengeFlag"] = relationship()

    challenge_instance: Mapped["ChallengeInstance"] = relationship()
