"""
db/runtime_models.py

Runtime models for The Pond database.

These tables track:
- challenges currently or previously launched by users
- actual cloned Proxmox VMs
- jobs handled by the Python Instance Manager

Relationships:

users
  |
  +---- challenge_instances
  |
  +---- instance_jobs

challenges
  |
  +---- challenge_instances

challenge_instances
  |
  +---- vm_instances
  |
  +---- instance_jobs

vm_templates
  |
  +---- vm_instances
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.orm import db


def utc_now():
    return datetime.now(timezone.utc)


class ChallengeInstance(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "challenge_instances"

    instance_id: Mapped[int] = mapped_column(
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

    challenge_id: Mapped[int] = mapped_column(
        ForeignKey("challenges.challenge_id"),
        nullable=False
    )

    instance_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="normal"
    )

    scoring_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="queued"
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    stopped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    network_identifier: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True
    )

    last_activity_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )

    user: Mapped["User"] = relationship()

    challenge: Mapped["Challenge"] = relationship()

    vm_instances: Mapped[list["VMInstance"]] = relationship(
        back_populates="challenge_instance",
        cascade="all, delete-orphan"
    )

    jobs: Mapped[list["InstanceJob"]] = relationship(
        back_populates="challenge_instance",
        cascade="all, delete-orphan"
    )


class VMInstance(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "vm_instances"

    vm_instance_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    instance_id: Mapped[int] = mapped_column(
        ForeignKey(
            "challenge_instances.instance_id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    template_id: Mapped[int] = mapped_column(
        ForeignKey("vm_templates.template_id"),
        nullable=False
    )

    proxmox_vmid: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True # changed from unique to allow vmid's to be reused
    )

    proxmox_node: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    hostname: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True
    )

    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45),
        nullable=True
    )

    mac_address: Mapped[Optional[str]] = mapped_column(
        String(17),
        nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="cloning"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    stopped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    challenge_instance: Mapped["ChallengeInstance"] = relationship(
        back_populates="vm_instances"
    )

    vm_template: Mapped["VMTemplate"] = relationship()


class InstanceJob(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "instance_jobs"

    job_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    instance_id: Mapped[int] = mapped_column(
        ForeignKey(
            "challenge_instances.instance_id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    requested_by_user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.user_id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    action: Mapped[str] = mapped_column(
        String(20),
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="queued"
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0
    )

    locked_by: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )

    challenge_instance: Mapped["ChallengeInstance"] = relationship(
        back_populates="jobs"
    )

    requested_by: Mapped["User"] = relationship()
