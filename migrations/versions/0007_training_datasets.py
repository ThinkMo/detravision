"""Persist selectable server training datasets."""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_dataset",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("root_alias", sa.String(length=32), nullable=False),
        sa.Column("directory", sa.String(length=512), nullable=False),
        sa.Column("train_split", sa.String(length=512), nullable=False),
        sa.Column("val_split", sa.String(length=512), nullable=False),
        sa.Column("test_split", sa.String(length=512), nullable=False),
        sa.Column("classes", sa.Text(), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
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
        sa.UniqueConstraint(
            "root_alias", "directory", name="uq_training_dataset_root_directory"
        ),
    )
    op.create_index(
        "ix_training_dataset_enabled", "training_dataset", ["enabled"]
    )
    with op.batch_alter_table("training_job") as batch_op:
        batch_op.add_column(sa.Column("training_dataset_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_training_job_training_dataset_id_training_dataset",
            "training_dataset",
            ["training_dataset_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_training_job_training_dataset_id", ["training_dataset_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("training_job") as batch_op:
        batch_op.drop_index("ix_training_job_training_dataset_id")
        batch_op.drop_constraint(
            "fk_training_job_training_dataset_id_training_dataset",
            type_="foreignkey",
        )
        batch_op.drop_column("training_dataset_id")
    op.drop_index("ix_training_dataset_enabled", table_name="training_dataset")
    op.drop_table("training_dataset")
