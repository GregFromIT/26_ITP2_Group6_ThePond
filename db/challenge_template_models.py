"""Challenge-specific use of a reusable VM template."""
from typing import Optional
from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from db.orm import db


class ChallengeTemplate(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "challenge_templates"
    __table_args__ = (UniqueConstraint("challenge_id", "vm_role", name="uq_challenge_template_role"),)
    challenge_template_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.challenge_id", ondelete="CASCADE"), nullable=False)
    template_id: Mapped[int] = mapped_column(ForeignKey("vm_templates.template_id", ondelete="RESTRICT"), nullable=False)
    vm_role: Mapped[str] = mapped_column(String(50), nullable=False)
    boot_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    static_ip: Mapped[Optional[str]] = mapped_column(String(45))
    hostname_prefix: Mapped[Optional[str]] = mapped_column(String(50))
    network_name: Mapped[Optional[str]] = mapped_column(String(100))
    is_user_accessible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    challenge: Mapped["Challenge"] = relationship(back_populates="template_assignments")
    template: Mapped["VMTemplate"] = relationship(back_populates="assignments")
