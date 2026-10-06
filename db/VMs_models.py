"""
db/VMs_models.py

VM template and flag models for The Pond database.

Challenges share reusable VM templates through challenge_templates.
Flags belong to challenges, with an optional source-template reference.
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

    assignments: Mapped[list["ChallengeTemplate"]] = relationship(back_populates="template")

    flags: Mapped[list["ChallengeFlag"]] = relationship(
        back_populates="vm_template", passive_deletes=True)


class ChallengeFlag(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "challenge_flags"
    challenge_id: Mapped[int] = mapped_column(
        ForeignKey("challenges.challenge_id", ondelete="CASCADE"), nullable=False, index=True)
    challenge: Mapped["Challenge"] = relationship()

    flag_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    template_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "vm_templates.template_id",
            ondelete="SET NULL"
        ),
        nullable=True
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

    vm_template: Mapped[Optional["VMTemplate"]] = relationship(
        back_populates="flags"
    )
