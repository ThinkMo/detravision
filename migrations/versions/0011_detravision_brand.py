"""Rename the untouched legacy site brand to DetraVision."""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE site_settings SET site_name = 'DetraVision' "
            "WHERE site_name = 'Safety Detect'"
        )
    )
    with op.batch_alter_table("site_settings") as batch_op:
        batch_op.alter_column(
            "site_name",
            existing_type=sa.String(length=200),
            existing_nullable=False,
            server_default=sa.text("'DetraVision'"),
        )


def downgrade() -> None:
    with op.batch_alter_table("site_settings") as batch_op:
        batch_op.alter_column(
            "site_name",
            existing_type=sa.String(length=200),
            existing_nullable=False,
            server_default=sa.text("'Safety Detect'"),
        )
    op.execute(
        sa.text(
            "UPDATE site_settings SET site_name = 'Safety Detect' "
            "WHERE site_name = 'DetraVision'"
        )
    )
