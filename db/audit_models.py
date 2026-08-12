"""
db/audit_models.py

Administrative audit logging for The Pond database.

Records important manager and sysadmin actions such as:
- password resets
- user creation/deletion
- challenge creation/modification
- VM template changes
- manual challenge termination
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.orm import db


def utc_now():
    return datetime.now(timezone.utc)


class AuditLog(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "audit_logs"

    audit_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    actor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "users.user_id",
            ondelete="SET NULL"
        ),
        nullable=True
    )

    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    target_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    target_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )

    details_json: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )

    actor: Mapped[Optional["User"]] = relationship()
