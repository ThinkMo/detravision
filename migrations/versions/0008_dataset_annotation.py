"""Version training datasets and persist image review state."""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("training_dataset") as batch_op:
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch_op.add_column(
            sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("('published')"))
        )
        batch_op.add_column(sa.Column("parent_id", sa.Integer()))
        batch_op.add_column(sa.Column("root_dataset_id", sa.Integer()))
        batch_op.add_column(
            sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(sa.Column("published_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("manifest_digest", sa.String(length=64)))
        batch_op.create_foreign_key(
            "fk_training_dataset_parent_id_training_dataset",
            "training_dataset", ["parent_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_training_dataset_root_dataset_id_training_dataset",
            "training_dataset", ["root_dataset_id"], ["id"], ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_training_dataset_root_version", ["root_dataset_id", "version"]
        )
        batch_op.create_check_constraint("ck_training_dataset_version_positive", "version > 0")
        batch_op.create_check_constraint("ck_training_dataset_revision_nonnegative", "revision >= 0")
        batch_op.create_check_constraint(
            "ck_training_dataset_status", "status IN ('editing','published')"
        )
        batch_op.create_index("ix_training_dataset_status", ["status"])
        batch_op.create_index(
            "ix_training_dataset_root_version", ["root_dataset_id", "version"]
        )
    op.execute("UPDATE training_dataset SET published_at = created_at WHERE published_at IS NULL")

    op.create_table(
        "training_dataset_image",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "dataset_id", sa.Integer(),
            sa.ForeignKey("training_dataset.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("split", sa.Text(), nullable=False),
        sa.Column("image_key", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column(
            "review_status", sa.Text(), nullable=False,
            server_default=sa.text("('pending_annotation')"),
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("validation_code", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "dataset_id", "split", "image_key", name="uq_training_dataset_image_key"
        ),
        sa.CheckConstraint("split IN ('train','val','test')", name="ck_training_dataset_image_split"),
        sa.CheckConstraint(
            "review_status IN ('pending_annotation','annotated','confirmed_empty')",
            name="ck_training_dataset_image_review_status",
        ),
        sa.CheckConstraint("width > 0 AND height > 0", name="ck_training_dataset_image_dimensions_positive"),
        sa.CheckConstraint("revision >= 0", name="ck_training_dataset_image_revision_nonnegative"),
    )
    op.create_index(
        "ix_training_dataset_image_filter", "training_dataset_image",
        ["dataset_id", "split", "review_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_training_dataset_image_filter", table_name="training_dataset_image")
    op.drop_table("training_dataset_image")
    with op.batch_alter_table("training_dataset") as batch_op:
        batch_op.drop_index("ix_training_dataset_root_version")
        batch_op.drop_index("ix_training_dataset_status")
        batch_op.drop_constraint("ck_training_dataset_status", type_="check")
        batch_op.drop_constraint("ck_training_dataset_revision_nonnegative", type_="check")
        batch_op.drop_constraint("ck_training_dataset_version_positive", type_="check")
        batch_op.drop_constraint("uq_training_dataset_root_version", type_="unique")
        batch_op.drop_constraint(
            "fk_training_dataset_root_dataset_id_training_dataset", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_training_dataset_parent_id_training_dataset", type_="foreignkey"
        )
        batch_op.drop_column("manifest_digest")
        batch_op.drop_column("published_at")
        batch_op.drop_column("revision")
        batch_op.drop_column("root_dataset_id")
        batch_op.drop_column("parent_id")
        batch_op.drop_column("status")
        batch_op.drop_column("version")
