"""Harden training snapshots and candidate registration uniqueness."""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("training_job") as batch_op:
        batch_op.add_column(sa.Column("dataset_classes", sa.Text()))
    with op.batch_alter_table("model_version") as batch_op:
        batch_op.create_unique_constraint(
            "uq_model_version_training_job_id", ["training_job_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("model_version") as batch_op:
        batch_op.drop_constraint(
            "uq_model_version_training_job_id", type_="unique"
        )
    with op.batch_alter_table("training_job") as batch_op:
        batch_op.drop_column("dataset_classes")
