"""Persist local training jobs and trained-model provenance."""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_job",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("executor", sa.String(length=20), nullable=False),
        sa.Column("base_model", sa.String(length=30), nullable=False),
        sa.Column("dataset_key", sa.String(length=100), nullable=False),
        sa.Column("epochs", sa.Integer(), nullable=False),
        sa.Column("imgsz", sa.Integer(), nullable=False),
        sa.Column("batch", sa.String(length=20), nullable=False),
        sa.Column("device", sa.String(length=20), nullable=False),
        sa.Column("patience", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("('queued')")),
        sa.Column("current_epoch", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_epochs", sa.Integer(), nullable=False),
        sa.Column("pid", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("stop_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("run_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("has_best", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("has_last", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("has_metrics", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("precision", sa.Float()),
        sa.Column("recall", sa.Float()),
        sa.Column("map50", sa.Float()),
        sa.Column("map50_95", sa.Float()),
        sa.Column("no_helmet_precision", sa.Float()),
        sa.Column("no_helmet_recall", sa.Float()),
        sa.Column("accepted", sa.Boolean()),
        sa.Column("metrics_json", sa.Text()),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("best_sha256", sa.String(length=64)),
        sa.Column(
            "resume_from_id",
            sa.Integer(),
            sa.ForeignKey("training_job.id", ondelete="SET NULL"),
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
            "status IN ('queued','running','evaluating','stopping','stopped','completed','failed','interrupted','cancelled')",
            name="status",
        ),
    )
    op.create_index("ix_training_job_status", "training_job", ["status"])
    op.create_index("ix_training_job_created_at", "training_job", ["created_at"])

    with op.batch_alter_table("model_version") as batch_op:
        batch_op.add_column(sa.Column("training_job_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_model_version_training_job_id_training_job",
            "training_job",
            ["training_job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.add_column(sa.Column("source", sa.String(length=20)))
        batch_op.add_column(sa.Column("base_model", sa.String(length=30)))
        batch_op.add_column(sa.Column("imgsz", sa.Integer()))
        batch_op.add_column(sa.Column("metrics_json", sa.Text()))
        batch_op.add_column(sa.Column("deployment_runtime", sa.String(length=50)))


def downgrade() -> None:
    with op.batch_alter_table("model_version") as batch_op:
        batch_op.drop_column("deployment_runtime")
        batch_op.drop_column("metrics_json")
        batch_op.drop_column("imgsz")
        batch_op.drop_column("base_model")
        batch_op.drop_column("source")
        batch_op.drop_constraint(
            "fk_model_version_training_job_id_training_job", type_="foreignkey"
        )
        batch_op.drop_column("training_job_id")
    op.drop_index("ix_training_job_created_at", table_name="training_job")
    op.drop_index("ix_training_job_status", table_name="training_job")
    op.drop_table("training_job")
