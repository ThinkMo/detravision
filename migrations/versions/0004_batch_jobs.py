"""Persist batch inference jobs and per-image results."""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batch_job",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("('queued')"),
        ),
        sa.Column("image_total", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'partial_failed', 'failed')",
            name="status",
        ),
        sa.CheckConstraint("image_total > 0", name="image_total_positive"),
        sa.CheckConstraint("success_count >= 0", name="success_count_nonnegative"),
        sa.CheckConstraint("failure_count >= 0", name="failure_count_nonnegative"),
        sa.CheckConstraint(
            "success_count + failure_count <= image_total",
            name="terminal_counts_within_total",
        ),
    )
    op.create_index(
        "ix_batch_job_user_created", "batch_job", ["user_id", "created_at"]
    )
    op.create_index("ix_batch_job_status", "batch_job", ["status"])
    op.create_index("ix_batch_job_expires_at", "batch_job", ["expires_at"])

    op.create_table(
        "batch_image",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("batch_job.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("annotated_key", sa.Text()),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("('queued')"),
        ),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "model_version_id",
            sa.Integer(),
            sa.ForeignKey("model_version.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "detections_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("('[]')"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="status",
        ),
        sa.CheckConstraint("length(display_name) > 0", name="display_name_nonempty"),
        sa.CheckConstraint("length(source_key) > 0", name="source_key_nonempty"),
        sa.CheckConstraint(
            "status != 'completed' OR annotated_key IS NOT NULL",
            name="completed_has_annotation",
        ),
        sa.CheckConstraint(
            "status != 'failed' OR error_code IS NOT NULL",
            name="failed_has_error_code",
        ),
    )
    op.create_index(
        "ix_batch_image_job_status", "batch_image", ["job_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_batch_image_job_status", table_name="batch_image")
    op.drop_table("batch_image")
    op.drop_index("ix_batch_job_expires_at", table_name="batch_job")
    op.drop_index("ix_batch_job_status", table_name="batch_job")
    op.drop_index("ix_batch_job_user_created", table_name="batch_job")
    op.drop_table("batch_job")
