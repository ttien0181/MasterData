from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from masterone.db.base import Base, now_utc


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320))
    email_normalized: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # §26.1 RBAC roles: VIEWER | STEWARD | SENIOR_STEWARD | POLICY_ADMIN | AUDITOR
    role: Mapped[str] = mapped_column(String(32), default="STEWARD", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at_epoch: Mapped[int] = mapped_column(Integer)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class LoginAttempt(Base):
    __tablename__ = "auth_login_attempts"
    __table_args__ = (Index("ix_auth_attempt_ip_time", "ip_address", "created_at_epoch"),
                      Index("ix_auth_attempt_email_ip_time", "email_normalized", "ip_address", "created_at_epoch"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email_normalized: Mapped[str] = mapped_column(String(320))
    ip_address: Mapped[str] = mapped_column(String(64))
    created_at_epoch: Mapped[int] = mapped_column(Integer)
