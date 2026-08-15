"""Add account, session, branding, and audit persistence."""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(length=100), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default=sa.text("'user'")),
        sa.CheckConstraint("role IN ('admin', 'user')", name="role"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "user_session",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_user_session_user_id", "user_session", ["user_id"])
    op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("site_name", sa.String(length=200), nullable=False, server_default=sa.text("'Safety Detect'")),
        sa.Column("logo_key", sa.Text()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.String(length=100)),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("details", sa.Text()),
        sa.Column("ip", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    with op.batch_alter_table("detection_event") as batch_op:
        batch_op.add_column(sa.Column("ack_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_detection_event_ack_user_id_user", "user", ["ack_user_id"], ["id"])
        batch_op.create_index("ix_detection_event_ack_user_id", ["ack_user_id"])


def downgrade() -> None:
    with op.batch_alter_table("detection_event") as batch_op:
        batch_op.drop_index("ix_detection_event_ack_user_id")
        batch_op.drop_constraint("fk_detection_event_ack_user_id_user", type_="foreignkey")
        batch_op.drop_column("ack_user_id")
    op.drop_index("ix_audit_log_actor_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("site_settings")
    op.drop_index("ix_user_session_user_id", table_name="user_session")
    op.drop_table("user_session")
    op.drop_table("user")
