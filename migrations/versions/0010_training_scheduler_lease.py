"""Persist scheduler leases and guarded training execution ownership."""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("training_scheduler") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.String(length=64)))
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
        batch_op.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )

    with op.batch_alter_table("training_job") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.String(length=64)))
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
        batch_op.add_column(
            sa.Column(
                "execution_generation",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("training_job") as batch_op:
        batch_op.drop_column("execution_generation")
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("owner_id")
    with op.batch_alter_table("training_scheduler") as batch_op:
        batch_op.drop_column("revision")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("owner_id")
