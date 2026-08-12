"""
db/user_models.py

SQLAlchemy models for user.db.

Relationships:

roles
  1
  |
  N
users
  1
  |
  1
user_credentials
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.orm import db


class Role(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "roles"

    role_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    role_name: Mapped[str] = mapped_column(
        String(20),
        unique=True,
        nullable=False
    )

    role_level: Mapped[int] = mapped_column(
        Integer,
        unique=True,
        nullable=False
    )

    description: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True
    )

    users: Mapped[list["User"]] = relationship(
        back_populates="role"
    )


class User(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False
    )

    display_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.role_id"),
        nullable=False
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )

    role: Mapped["Role"] = relationship(
        back_populates="users"
    )

    credentials: Mapped[Optional["UserCredential"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False
    )


class UserCredential(db.Model):
    __bind_key__ = "pond"
    __tablename__ = "user_credentials"

    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.user_id",
            ondelete="CASCADE"
        ),
        primary_key=True
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False
    )

    failed_login_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0
    )

    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )

    user: Mapped["User"] = relationship(
        back_populates="credentials"
    )

