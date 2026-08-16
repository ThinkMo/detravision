from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, String, Table, Text, Column, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UtcDateTime


alarm_group_member = Table(
    "alarm_group_member",
    Base.metadata,
    Column("group_id", ForeignKey("alarm_group.id", ondelete="CASCADE"), primary_key=True),
    Column("person_id", ForeignKey("person.id", ondelete="CASCADE"), primary_key=True),
)


class Camera(Base):
    __tablename__ = "camera"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    rtsp_url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    sample_fps: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"))
    roi: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("('[]')"))
    detect_classes: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("('[\"no_helmet\",\"fire\",\"smoke\"]')")
    )
    alarm_group_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class Person(Base):
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    feishu_id: Mapped[str | None] = mapped_column(Text)
    dept: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class AlarmGroup(Base):
    __tablename__ = "alarm_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class DetectionEvent(Base):
    __tablename__ = "detection_event"
    __table_args__ = (
        CheckConstraint("status IN ('new', 'ack', 'closed')", name="status"),
        Index("ix_event_camera_created", "camera_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("camera.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bbox: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("('new')"))
    ack_by: Mapped[int | None] = mapped_column(ForeignKey("person.id"))
    ack_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ModelVersion(Base):
    __tablename__ = "model_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    runtime: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("('onnx-fp32')"))
    classes: Mapped[str] = mapped_column(Text, nullable=False)
    map50: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class User(Base):
    __tablename__ = "user"
    __table_args__ = (CheckConstraint("role IN ('admin', 'user')", name="role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'user'"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("1"))
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class UserSession(Base):
    __tablename__ = "user_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class SiteSettings(Base):
    __tablename__ = "site_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_name: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("'Safety Detect'"))
    logo_key: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class LogoCleanupQueue(Base):
    __tablename__ = "logo_cleanup_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    logo_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
