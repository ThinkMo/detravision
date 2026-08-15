"""Create the schema used by the original SQLite implementation."""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "camera",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("rtsp_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("sample_fps", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("roi", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("detect_classes", sa.Text(), nullable=False, server_default=sa.text("'[\"no_helmet\",\"fire\",\"smoke\"]'")),
        sa.Column("alarm_group_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "person",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text()),
        sa.Column("feishu_id", sa.Text()),
        sa.Column("dept", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "alarm_group",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "alarm_group_member",
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("alarm_group.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("group_id", "person_id"),
    )
    op.create_table(
        "detection_event",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.Integer(), sa.ForeignKey("camera.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("bbox", sa.Text(), nullable=False),
        sa.Column("snapshot_path", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'new'")),
        sa.CheckConstraint("status IN ('new','ack','closed')", name="status"),
        sa.Column("ack_by", sa.Integer(), sa.ForeignKey("person.id")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_event_camera_created", "detection_event", ["camera_id", "created_at"])
    op.create_table(
        "model_version",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("runtime", sa.Text(), nullable=False, server_default=sa.text("'onnx-fp32'")),
        sa.Column("classes", sa.Text(), nullable=False),
        sa.Column("map50", sa.Float()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("model_version")
    op.drop_index("ix_event_camera_created", table_name="detection_event")
    op.drop_table("detection_event")
    op.drop_table("alarm_group_member")
    op.drop_table("alarm_group")
    op.drop_table("person")
    op.drop_table("camera")
