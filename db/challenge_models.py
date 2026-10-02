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

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.orm import db


def utc_now():
    return datetime.now(timezone.utc)


class Challenge(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "challenges"
    __table_args__ = (
        CheckConstraint("execution_type IN ('vm', 'container_lab', 'offline')", name="ck_challenge_execution_type"),
        CheckConstraint("(execution_type = 'container_lab' AND docker_challenge_key IS NOT NULL "
                        "AND length(trim(docker_challenge_key)) > 0) OR "
                        "(execution_type <> 'container_lab' AND docker_challenge_key IS NULL)",
                        name="ck_challenge_docker_key"),
    )
    execution_type: Mapped[str] = mapped_column(String(20), nullable=False, default="vm", server_default="vm")
    docker_challenge_key: Mapped[Optional[str]] = mapped_column(String(160))

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

    template_assignments: Mapped[list["ChallengeTemplate"]] = relationship(
        back_populates="challenge",
        cascade="all, delete-orphan"
    )

class NetworkRule(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "network_rules"

    rule_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    challenge_id: Mapped[int] = mapped_column(
        ForeignKey(
            "challenges.challenge_id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    from_role: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    to_role: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    protocol: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="tcp"
    )

    port: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )