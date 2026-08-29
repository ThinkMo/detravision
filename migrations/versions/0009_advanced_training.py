"""Persist immutable advanced training and acceptance snapshots."""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


LEGACY_ADVANCED_CONFIG = {
    "amp": True,
    "box": 7.5,
    "cache": "false",
    "close_mosaic": 10,
    "cls": 0.5,
    "cos_lr": False,
    "degrees": 0.0,
    "deterministic": True,
    "dfl": 1.5,
    "fliplr": 0.5,
    "flipud": 0.0,
    "freeze": 0,
    "hsv_h": 0.015,
    "hsv_s": 0.7,
    "hsv_v": 0.4,
    "lr0": 0.01,
    "lrf": 0.01,
    "mixup": 0.0,
    "momentum": 0.937,
    "mosaic": 1.0,
    "optimizer": "auto",
    "perspective": 0.0,
    "scale": 0.5,
    "seed": 0,
    "shear": 0.0,
    "translate": 0.1,
    "warmup_bias_lr": 0.1,
    "warmup_epochs": 3.0,
    "warmup_momentum": 0.8,
    "weight_decay": 0.0005,
    "workers": 0,
}
LEGACY_ACCEPTANCE_CONFIG = {
    "schema_version": 1,
    "overall": {"map50": 0.85},
    "classes": {"no_helmet": {"precision": 0.85, "recall": 0.9}},
}


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def upgrade() -> None:
    with op.batch_alter_table("training_job") as batch_op:
        batch_op.add_column(sa.Column("config_schema_version", sa.Integer()))
        batch_op.add_column(sa.Column("training_profile", sa.String(length=30)))
        batch_op.add_column(sa.Column("advanced_config", sa.Text()))
        batch_op.add_column(sa.Column("acceptance_config", sa.Text()))

    training_job = sa.table(
        "training_job",
        sa.column("config_schema_version", sa.Integer()),
        sa.column("training_profile", sa.String(length=30)),
        sa.column("advanced_config", sa.Text()),
        sa.column("acceptance_config", sa.Text()),
        sa.column("epochs", sa.Integer()),
    )
    op.execute(
        training_job.update().values(
            config_schema_version=1,
            training_profile="legacy",
            advanced_config=_compact_json(LEGACY_ADVANCED_CONFIG),
            acceptance_config=_compact_json(LEGACY_ACCEPTANCE_CONFIG),
        )
    )
    for epoch_count in range(10):
        compatible_config = {
            **LEGACY_ADVANCED_CONFIG,
            "close_mosaic": epoch_count,
        }
        op.execute(
            training_job.update()
            .where(training_job.c.epochs == epoch_count)
            .values(advanced_config=_compact_json(compatible_config))
        )

    with op.batch_alter_table("training_job") as batch_op:
        batch_op.alter_column(
            "config_schema_version", existing_type=sa.Integer(), nullable=False
        )
        batch_op.alter_column(
            "training_profile", existing_type=sa.String(length=30), nullable=False
        )
        batch_op.alter_column(
            "advanced_config", existing_type=sa.Text(), nullable=False
        )
        batch_op.alter_column(
            "acceptance_config", existing_type=sa.Text(), nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("training_job") as batch_op:
        batch_op.drop_column("acceptance_config")
        batch_op.drop_column("advanced_config")
        batch_op.drop_column("training_profile")
        batch_op.drop_column("config_schema_version")
