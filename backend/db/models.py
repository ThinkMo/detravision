from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, String, Table, Text, Column, UniqueConstraint, text
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
    training_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("training_job.id", ondelete="SET NULL"), unique=True
    )
    source: Mapped[str | None] = mapped_column(String(20))
    base_model: Mapped[str | None] = mapped_column(String(30))
    imgsz: Mapped[int | None] = mapped_column(Integer)
    metrics_json: Mapped[str | None] = mapped_column(Text)
    deployment_runtime: Mapped[str | None] = mapped_column(String(50))
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


class TrainingDataset(Base):
    __tablename__ = "training_dataset"
    __table_args__ = (
        UniqueConstraint(
            "root_alias", "directory", name="uq_training_dataset_root_directory"
        ),
        UniqueConstraint(
            "root_dataset_id", "version", name="uq_training_dataset_root_version"
        ),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint("status IN ('editing','published')", name="status"),
        Index("ix_training_dataset_enabled", "enabled"),
        Index("ix_training_dataset_status", "status"),
        Index("ix_training_dataset_root_version", "root_dataset_id", "version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    root_alias: Mapped[str] = mapped_column(String(32), nullable=False)
    directory: Mapped[str] = mapped_column(String(512), nullable=False)
    train_split: Mapped[str] = mapped_column(String(512), nullable=False)
    val_split: Mapped[str] = mapped_column(String(512), nullable=False)
    test_split: Mapped[str] = mapped_column(String(512), nullable=False)
    classes: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("('published')")
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("training_dataset.id", ondelete="RESTRICT")
    )
    root_dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("training_dataset.id", ondelete="RESTRICT")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    published_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    manifest_digest: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class TrainingDatasetImage(Base):
    __tablename__ = "training_dataset_image"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id", "split", "image_key",
            name="uq_training_dataset_image_key",
        ),
        CheckConstraint("split IN ('train','val','test')", name="split"),
        CheckConstraint(
            "review_status IN ('pending_annotation','annotated','confirmed_empty')",
            name="review_status",
        ),
        CheckConstraint("width > 0 AND height > 0", name="dimensions_positive"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index("ix_training_dataset_image_filter", "dataset_id", "split", "review_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("training_dataset.id", ondelete="CASCADE"), nullable=False
    )
    split: Mapped[str] = mapped_column(Text, nullable=False)
    image_key: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("('pending_annotation')")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class TrainingJob(Base):
    __tablename__ = "training_job"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','evaluating','stopping','stopped','completed','failed','interrupted','cancelled')",
            name="status",
        ),
        Index("ix_training_job_status", "status"),
        Index("ix_training_job_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_by: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    executor: Mapped[str] = mapped_column(String(20), nullable=False)
    base_model: Mapped[str] = mapped_column(String(30), nullable=False)
    dataset_key: Mapped[str] = mapped_column(String(100), nullable=False)
    dataset_classes: Mapped[str | None] = mapped_column(Text)
    training_dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("training_dataset.id", ondelete="SET NULL"), index=True
    )
    epochs: Mapped[int] = mapped_column(Integer, nullable=False)
    imgsz: Mapped[int] = mapped_column(Integer, nullable=False)
    batch: Mapped[str] = mapped_column(String(20), nullable=False)
    device: Mapped[str] = mapped_column(String(20), nullable=False)
    patience: Mapped[int] = mapped_column(Integer, nullable=False)
    config_schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    training_profile: Mapped[str] = mapped_column(
        String(30), nullable=False, default="legacy"
    )
    advanced_config: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    acceptance_config: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("('queued')")
    )
    current_epoch: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_epochs: Mapped[int] = mapped_column(Integer, nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer)
    owner_id: Mapped[str | None] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    execution_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    stop_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )
    run_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    has_best: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    has_last: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    has_metrics: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("0"))
    precision: Mapped[float | None] = mapped_column(Float)
    recall: Mapped[float | None] = mapped_column(Float)
    map50: Mapped[float | None] = mapped_column(Float)
    map50_95: Mapped[float | None] = mapped_column(Float)
    no_helmet_precision: Mapped[float | None] = mapped_column(Float)
    no_helmet_recall: Mapped[float | None] = mapped_column(Float)
    accepted: Mapped[bool | None] = mapped_column(Boolean)
    metrics_json: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    best_sha256: Mapped[str | None] = mapped_column(String(64))
    resume_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("training_job.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class TrainingScheduler(Base):
    """Singleton row used to serialize MySQL training-job claims."""

    __tablename__ = "training_scheduler"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    lease_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


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


class BatchJob(Base):
    __tablename__ = "batch_job"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'partial_failed', 'failed')",
            name="status",
        ),
        CheckConstraint("image_total > 0", name="image_total_positive"),
        CheckConstraint("success_count >= 0", name="success_count_nonnegative"),
        CheckConstraint("failure_count >= 0", name="failure_count_nonnegative"),
        CheckConstraint(
            "success_count + failure_count <= image_total",
            name="terminal_counts_within_total",
        ),
        Index("ix_batch_job_user_created", "user_id", "created_at"),
        Index("ix_batch_job_status", "status"),
        Index("ix_batch_job_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("('queued')")
    )
    image_total: Mapped[int] = mapped_column(Integer, nullable=False)
    success_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class BatchImage(Base):
    __tablename__ = "batch_image"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="status",
        ),
        CheckConstraint("length(display_name) > 0", name="display_name_nonempty"),
        CheckConstraint("length(source_key) > 0", name="source_key_nonempty"),
        CheckConstraint(
            "status != 'completed' OR annotated_key IS NOT NULL",
            name="completed_has_annotation",
        ),
        CheckConstraint(
            "status != 'failed' OR error_code IS NOT NULL",
            name="failed_has_error_code",
        ),
        Index("ix_batch_image_job_status", "job_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("batch_job.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    annotated_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("('queued')")
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_version.id", ondelete="SET NULL")
    )
    detections_json: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("('[]')")
    )
    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
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
