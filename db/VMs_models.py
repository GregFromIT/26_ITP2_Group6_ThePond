"""
db/VMs_models.py

VM template and flag models for The Pond database.

Relationships:

challenges
  1
  |
  N
vm_templates
  1
  |
  N
challenge_flags
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.orm import db


def utc_now():
    return datetime.now(timezone.utc)


class VMTemplate(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "vm_templates"

    template_id: Mapped[int] = mapped_column(
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

    template_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    proxmox_template_vmid: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        unique=True
    )

    proxmox_node: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    snapshot_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True
    )

    vm_role: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    cpu_cores: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1
    )

    memory_mb: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1024
    )

    disk_gb: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )

    boot_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1
    )

    hostname_prefix: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True
    )

    network_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True
    )

    is_user_accessible: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )

    challenge: Mapped["Challenge"] = relationship(
        back_populates="vm_templates"
    )

    flags: Mapped[list["ChallengeFlag"]] = relationship(
        back_populates="vm_template",
        cascade="all, delete-orphan"
    )


class ChallengeFlag(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "challenge_flags"

    flag_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    template_id: Mapped[int] = mapped_column(
        ForeignKey(
            "vm_templates.template_id",
            ondelete="CASCADE"
        ),
        nullable=False
    )

    flag_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    flag_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    points: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    sequence_number: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )

    vm_template: Mapped["VMTemplate"] = relationship(
        back_populates="flags"
    )
