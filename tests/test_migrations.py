from __future__ import annotations

import os
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace

import pytest
from alembic import command
from sqlalchemy import Text, create_engine, inspect, select, text
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.engine import make_url

from backend.db import migrate
from backend.db.migrate import alembic_config, run_migrations
from backend.db.models import (
    BatchImage,
    Camera,
    DetectionEvent,
    ModelVersion,
    TrainingDataset,
    TrainingDatasetImage,
    TrainingJob,
    TrainingScheduler,
    UserSession,
)
from backend.db.session import create_engine_from_url, create_session_factory


LEGACY_SCHEMA = """
CREATE TABLE camera (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    rtsp_url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    sample_fps REAL NOT NULL DEFAULT 1.0,
    roi TEXT NOT NULL DEFAULT '[]',
    detect_classes TEXT NOT NULL DEFAULT '[\"no_helmet\",\"fire\",\"smoke\"]',
    alarm_group_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE person (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    feishu_id TEXT,
    dept TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE alarm_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE alarm_group_member (
    group_id INTEGER NOT NULL REFERENCES alarm_group(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, person_id)
);
CREATE TABLE detection_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id INTEGER NOT NULL REFERENCES camera(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    bbox TEXT NOT NULL,
    snapshot_path TEXT,
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','ack','closed')),
    ack_by INTEGER REFERENCES person(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE model_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    runtime TEXT NOT NULL DEFAULT 'onnx-fp32',
    classes TEXT NOT NULL,
    map50 REAL,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_event_camera_created ON detection_event(camera_id, created_at DESC);
"""


@pytest.fixture
def legacy_database_url(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(LEGACY_SCHEMA)
    return "sqlite:///" + path.as_posix()


@pytest.fixture
def mysql_url():
    url = os.getenv("TEST_MYSQL_URL")
    if url is None:
        pytest.skip("TEST_MYSQL_URL is not configured")
    return url


def test_fresh_sqlite_upgrade_creates_all_tables(tmp_path):
    """Catches a revision chain that omits a required legacy or auth table."""
    url = "sqlite:///" + (tmp_path / "fresh.db").as_posix()

    run_migrations(url)

    names = set(inspect(create_engine(url)).get_table_names())
    assert {"camera", "person", "alarm_group", "detection_event", "model_version"} <= names
    assert {
        "user",
        "user_session",
        "site_settings",
        "audit_log",
        "logo_cleanup_queue",
        "batch_job",
        "batch_image",
        "training_job",
        "training_scheduler",
    } <= names


def test_batch_migration_has_portable_foreign_keys_and_query_indexes(tmp_path):
    """Catches missing ownership/result lineage or indexes needed by job workers."""
    url = "sqlite:///" + (tmp_path / "batch-schema.db").as_posix()
    run_migrations(url)
    inspector = inspect(create_engine(url))

    assert {fk["referred_table"] for fk in inspector.get_foreign_keys("batch_job")} == {
        "user"
    }
    assert {
        fk["referred_table"] for fk in inspector.get_foreign_keys("batch_image")
    } == {"batch_job", "model_version"}
    job_indexes = {
        tuple(index["column_names"]) for index in inspector.get_indexes("batch_job")
    }
    image_indexes = {
        tuple(index["column_names"]) for index in inspector.get_indexes("batch_image")
    }
    assert {("user_id", "created_at"), ("status",), ("expires_at",)} <= job_indexes
    assert {("job_id", "status"),} <= image_indexes


def test_batch_result_and_status_fields_remain_text_in_metadata():
    """Catches introducing dialect-specific enums or JSON columns."""
    assert isinstance(BatchImage.__table__.c.status.type, Text)
    assert isinstance(BatchImage.__table__.c.detections_json.type, Text)


def test_training_migration_adds_queue_indexes_and_model_provenance(tmp_path):
    """Catches missing queue indexes or provenance needed to activate trained models."""
    url = "sqlite:///" + (tmp_path / "training-schema.db").as_posix()
    run_migrations(url)
    inspector = inspect(create_engine(url))

    assert "training_job" in inspector.get_table_names()
    assert "training_scheduler" in inspector.get_table_names()
    indexes = {
        tuple(index["column_names"])
        for index in inspector.get_indexes("training_job")
    }
    assert {("status",), ("created_at",)} <= indexes
    foreign_keys = {
        (tuple(foreign_key["constrained_columns"]), foreign_key["referred_table"])
        for foreign_key in inspector.get_foreign_keys("training_job")
    }
    assert {(('created_by',), 'user'), (('resume_from_id',), 'training_job')} <= foreign_keys
    model_columns = {
        column["name"]: column for column in inspector.get_columns("model_version")
    }
    assert {
        "training_job_id",
        "source",
        "base_model",
        "imgsz",
        "metrics_json",
        "deployment_runtime",
    } <= model_columns.keys()
    assert model_columns["training_job_id"]["nullable"] is True
    job_columns = {
        column["name"]: column for column in inspector.get_columns("training_job")
    }
    assert "dataset_classes" in job_columns
    assert isinstance(TrainingJob.__table__.c.dataset_classes.type, Text)
    unique_training_models = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("model_version")
    }
    assert ("training_job_id",) in unique_training_models
    with create_engine(url).connect() as connection:
        assert connection.execute(text("select id from training_scheduler")).scalar_one() == 1


def test_training_job_uses_portable_text_status_and_metrics():
    """Catches dialect-specific enum or JSON types that break SQLite/MySQL parity."""
    assert isinstance(TrainingJob.__table__.c.status.type, Text)
    assert isinstance(TrainingJob.__table__.c.metrics_json.type, Text)
    assert isinstance(TrainingJob.__table__.c.advanced_config.type, Text)
    assert isinstance(TrainingJob.__table__.c.acceptance_config.type, Text)
    assert isinstance(ModelVersion.__table__.c.metrics_json.type, Text)
    assert ModelVersion.__table__.c.training_job_id.nullable is True
    assert TrainingScheduler.__table__.c.id.primary_key is True


def test_advanced_training_migration_backfills_immutable_snapshots(tmp_path):
    """Catches historical jobs becoming unreadable after snapshots become non-null."""
    url = "sqlite:///" + (tmp_path / "advanced-training.db").as_posix()
    config = alembic_config(url)
    command.upgrade(config, "0008")
    with create_engine(url).begin() as connection:
        connection.execute(text(
            "INSERT INTO user (username, password_hash, role, enabled, must_change_password) "
            "VALUES ('legacy-admin', 'unused', 'admin', 1, 0)"
        ))
        connection.execute(text(
            "INSERT INTO training_job "
            "(created_by, executor, base_model, dataset_key, epochs, imgsz, batch, "
            "device, patience, total_epochs, run_key, idempotency_key) VALUES "
            "(1, 'local', 'yolo11n.pt', 'safety', 3, 640, '4', '0', 2, 3, "
            "'legacy-run', 'legacy-request')"
        ))

    command.upgrade(config, "0009")

    engine = create_engine(url)
    columns = {
        column["name"]: column for column in inspect(engine).get_columns("training_job")
    }
    assert {
        "config_schema_version", "training_profile", "advanced_config",
        "acceptance_config",
    } <= columns.keys()
    assert all(
        columns[name]["nullable"] is False
        for name in (
            "config_schema_version", "training_profile", "advanced_config",
            "acceptance_config",
        )
    )
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT config_schema_version, training_profile, advanced_config, "
            "acceptance_config FROM training_job WHERE run_key = 'legacy-run'"
        )).mappings().one()
    assert row["config_schema_version"] == 1
    assert row["training_profile"] == "legacy"
    advanced_config = json.loads(row["advanced_config"])
    assert advanced_config["cache"] == "false"
    assert advanced_config["close_mosaic"] == 3
    assert row["acceptance_config"] == (
        '{"classes":{"no_helmet":{"precision":0.85,"recall":0.9}},'
        '"overall":{"map50":0.85},"schema_version":1}'
    )


def test_advanced_training_migration_downgrades_and_reupgrades(tmp_path):
    """Catches snapshot columns making the SQLite migration chain one-way."""
    url = "sqlite:///" + (tmp_path / "advanced-training-roundtrip.db").as_posix()
    config = alembic_config(url)
    command.upgrade(config, "0009")
    command.downgrade(config, "0008")
    assert "advanced_config" not in {
        column["name"] for column in inspect(create_engine(url)).get_columns("training_job")
    }

    command.upgrade(config, "0009")
    assert "advanced_config" in {
        column["name"] for column in inspect(create_engine(url)).get_columns("training_job")
    }


def test_mysql_advanced_training_migration_is_offline_portable():
    """Catches SQLite-only snapshot DDL or MySQL-incompatible JSON storage."""
    output = StringIO()
    config = alembic_config(
        "mysql+pymysql://safety:safety@127.0.0.1/safety_test?charset=utf8mb4"
    )
    config.output_buffer = output

    command.upgrade(config, "0009", sql=True)

    sql = output.getvalue()
    assert "config_schema_version INTEGER NOT NULL" in sql
    assert "training_profile VARCHAR(30) NOT NULL" in sql
    assert "advanced_config TEXT NOT NULL" in sql
    assert "acceptance_config TEXT NOT NULL" in sql


def test_training_scheduler_lease_migration_adds_safe_ownership_columns(tmp_path):
    """Catches deployments without durable scheduler and task ownership state."""
    url = "sqlite:///" + (tmp_path / "training-lease.db").as_posix()
    run_migrations(url)
    inspector = inspect(create_engine(url))

    scheduler = {column["name"]: column for column in inspector.get_columns("training_scheduler")}
    jobs = {column["name"]: column for column in inspector.get_columns("training_job")}
    assert {"owner_id", "heartbeat_at", "lease_expires_at", "revision"} <= scheduler.keys()
    assert {"owner_id", "heartbeat_at", "execution_generation"} <= jobs.keys()
    assert scheduler["revision"]["nullable"] is False
    assert jobs["execution_generation"]["nullable"] is False

    with create_engine(url).connect() as connection:
        row = connection.execute(text(
            "SELECT owner_id, heartbeat_at, lease_expires_at, revision "
            "FROM training_scheduler WHERE id = 1"
        )).mappings().one()
    assert dict(row) == {
        "owner_id": None,
        "heartbeat_at": None,
        "lease_expires_at": None,
        "revision": 0,
    }


def test_mysql_training_scheduler_lease_migration_is_offline_portable():
    """Catches lease DDL that cannot be applied to production MySQL."""
    output = StringIO()
    config = alembic_config(
        "mysql+pymysql://safety:safety@127.0.0.1/safety_test?charset=utf8mb4"
    )
    config.output_buffer = output

    command.upgrade(config, "head", sql=True)

    sql = output.getvalue()
    assert "owner_id VARCHAR(64)" in sql
    assert "lease_expires_at DATETIME" in sql
    assert "heartbeat_at DATETIME" in sql
    assert "revision INTEGER" in sql
    assert "execution_generation INTEGER" in sql


def test_training_dataset_migration_adds_definition_and_job_reference(tmp_path):
    """Catches losing selectable dataset definitions or job provenance."""
    url = "sqlite:///" + (tmp_path / "training-dataset-schema.db").as_posix()
    run_migrations(url)
    inspector = inspect(create_engine(url))

    assert "training_dataset" in inspector.get_table_names()
    columns = {
        column["name"]: column
        for column in inspector.get_columns("training_dataset")
    }
    assert {
        "id", "name", "root_alias", "directory", "train_split", "val_split",
        "test_split", "classes", "enabled", "created_by", "created_at", "updated_at",
    } <= columns.keys()
    assert isinstance(columns["classes"]["type"], Text)
    unique_locations = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("training_dataset")
    }
    assert ("root_alias", "directory") in unique_locations
    job_foreign_keys = {
        (tuple(item["constrained_columns"]), item["referred_table"])
        for item in inspector.get_foreign_keys("training_job")
    }
    assert (("training_dataset_id",), "training_dataset") in job_foreign_keys


def test_dataset_annotation_migration_versions_datasets_and_indexes_images(tmp_path):
    """Catches mutable published datasets or an annotation index without revision control."""
    url = "sqlite:///" + (tmp_path / "dataset-annotation.db").as_posix()
    run_migrations(url)
    inspector = inspect(create_engine(url))

    dataset_columns = {
        column["name"] for column in inspector.get_columns("training_dataset")
    }
    assert {
        "version", "status", "parent_id", "root_dataset_id", "revision",
        "published_at", "manifest_digest",
    } <= dataset_columns
    assert "training_dataset_image" in inspector.get_table_names()
    image_columns = {
        column["name"] for column in inspector.get_columns("training_dataset_image")
    }
    assert {
        "dataset_id", "split", "image_key", "display_name", "width", "height",
        "review_status", "revision", "content_digest", "validation_code",
    } <= image_columns
    assert isinstance(TrainingDataset.__table__.c.status.type, Text)
    assert isinstance(TrainingDatasetImage.__table__.c.review_status.type, Text)


def test_mysql_dataset_annotation_migration_is_offline_portable():
    """Catches SQLite-only version or image-review DDL."""
    output = StringIO()
    config = alembic_config(
        "mysql+pymysql://safety:safety@127.0.0.1/safety_test?charset=utf8mb4"
    )
    config.output_buffer = output

    command.upgrade(config, "0008", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE training_dataset_image" in sql
    assert "review_status TEXT NOT NULL DEFAULT ('pending_annotation')" in sql
    assert "ALTER TABLE training_dataset" in sql
    assert "manifest_digest VARCHAR(64)" in sql


def test_dataset_annotation_migration_downgrades_and_reupgrades(tmp_path):
    """Catches a version migration that cannot be rolled back on local SQLite."""
    url = "sqlite:///" + (tmp_path / "dataset-annotation-roundtrip.db").as_posix()
    config = alembic_config(url)
    command.upgrade(config, "0008")
    command.downgrade(config, "0007")
    downgraded = inspect(create_engine(url))
    assert "training_dataset_image" not in downgraded.get_table_names()
    assert "status" not in {
        column["name"] for column in downgraded.get_columns("training_dataset")
    }

    command.upgrade(config, "0008")
    upgraded = inspect(create_engine(url))
    assert "training_dataset_image" in upgraded.get_table_names()


def test_mysql_training_dataset_migration_is_offline_portable():
    """Catches SQLite-only DDL or MySQL-incompatible JSON/default fields."""
    output = StringIO()
    config = alembic_config(
        "mysql+pymysql://safety:safety@127.0.0.1/safety_test?charset=utf8mb4"
    )
    config.output_buffer = output

    command.upgrade(config, "0007", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE training_dataset" in sql
    assert "classes TEXT NOT NULL" in sql
    assert "enabled BOOL NOT NULL DEFAULT 1" in sql
    assert "UNIQUE (root_alias, directory)" in sql
    assert "training_dataset_id INTEGER" in sql


def test_training_dataset_migration_downgrades_and_reupgrades(tmp_path):
    """Catches a migration that cannot safely roll back to the prior head."""
    url = "sqlite:///" + (tmp_path / "training-dataset-roundtrip.db").as_posix()
    config = alembic_config(url)
    command.upgrade(config, "0007")
    command.downgrade(config, "0006")

    inspector = inspect(create_engine(url))
    assert "training_dataset" not in inspector.get_table_names()
    assert "training_dataset_id" not in {
        column["name"] for column in inspector.get_columns("training_job")
    }

    command.upgrade(config, "0007")
    inspector = inspect(create_engine(url))
    assert "training_dataset" in inspector.get_table_names()
    assert "training_dataset_id" in {
        column["name"] for column in inspector.get_columns("training_job")
    }


def test_mysql_training_migration_emits_portable_text_defaults():
    """Catches MySQL-incompatible defaults in the training migration."""
    output = StringIO()
    config = alembic_config("mysql+pymysql://safety:safety@127.0.0.1/safety_test?charset=utf8mb4")
    config.output_buffer = output

    command.upgrade(config, "0005", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE training_job" in sql
    assert "CREATE TABLE training_scheduler" in sql
    assert "INSERT INTO training_scheduler" in sql
    assert "status TEXT NOT NULL DEFAULT ('queued')" in sql
    assert "metrics_json TEXT" in sql


def test_mysql_training_hardening_migration_is_offline_portable():
    """Catches SQLite-only batch DDL or a missing MySQL uniqueness guarantee."""
    output = StringIO()
    config = alembic_config(
        "mysql+pymysql://safety:safety@127.0.0.1/safety_test?charset=utf8mb4"
    )
    config.output_buffer = output

    command.upgrade(config, "0006", sql=True)

    sql = output.getvalue()
    assert "dataset_classes TEXT" in sql
    assert "UNIQUE (training_job_id)" in sql


def test_training_hardening_migration_downgrades_and_reupgrades_sqlite(tmp_path):
    """Catches a non-reversible SQLite batch migration for local development."""
    url = "sqlite:///" + (tmp_path / "training-hardening-round-trip.db").as_posix()
    config = alembic_config(url)
    command.upgrade(config, "head")

    command.downgrade(config, "0005")

    downgraded = inspect(create_engine(url))
    assert "dataset_classes" not in {
        column["name"] for column in downgraded.get_columns("training_job")
    }
    assert ("training_job_id",) not in {
        tuple(item["column_names"])
        for item in downgraded.get_unique_constraints("model_version")
    }

    command.upgrade(config, "head")
    upgraded = inspect(create_engine(url))
    assert "dataset_classes" in {
        column["name"] for column in upgraded.get_columns("training_job")
    }
    assert ("training_job_id",) in {
        tuple(item["column_names"])
        for item in upgraded.get_unique_constraints("model_version")
    }


def test_training_migration_can_downgrade_and_reupgrade_sqlite(tmp_path):
    """Catches a one-way migration that cannot safely rebuild local development databases."""
    url = "sqlite:///" + (tmp_path / "training-round-trip.db").as_posix()
    config = alembic_config(url)
    command.upgrade(config, "head")

    command.downgrade(config, "0004")

    downgraded = inspect(create_engine(url))
    assert "training_job" not in downgraded.get_table_names()
    assert "training_scheduler" not in downgraded.get_table_names()
    assert "training_job_id" not in {
        column["name"] for column in downgraded.get_columns("model_version")
    }

    command.upgrade(config, "head")

    upgraded = inspect(create_engine(url))
    assert {"training_job", "training_scheduler"} <= set(upgraded.get_table_names())
    assert "training_job_id" in {
        column["name"] for column in upgraded.get_columns("model_version")
    }


def test_legacy_sqlite_is_stamped_then_upgraded(legacy_database_url):
    """Catches treating an existing MVP database as a fresh empty database."""
    run_migrations(legacy_database_url)

    with create_engine(legacy_database_url).connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0010"
        event_columns = {column["name"] for column in inspect(connection).get_columns("detection_event")}
    assert {"ack_by", "ack_user_id"} <= event_columns


def test_incompatible_legacy_schema_is_rejected_before_stamping(tmp_path):
    """Catches accepting a partial legacy table and corrupting its upgrade path."""
    path = tmp_path / "incompatible.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(LEGACY_SCHEMA.replace("    rtsp_url TEXT NOT NULL,\n", ""))

    with pytest.raises(RuntimeError, match=r"camera.*rtsp_url"):
        run_migrations("sqlite:///" + path.as_posix())


def test_mysql_baseline_sql_uses_parenthesized_text_defaults():
    """Catches MySQL baseline DDL that loses legacy Text types or expression defaults."""
    output = StringIO()
    config = alembic_config("mysql+pymysql://safety:safety@127.0.0.1/safety_test?charset=utf8mb4")
    config.output_buffer = output

    command.upgrade(config, "0001", sql=True)

    sql = output.getvalue()
    assert "roi TEXT NOT NULL DEFAULT ('[]')" in sql
    assert "detect_classes TEXT NOT NULL DEFAULT ('[\"no_helmet\",\"fire\",\"smoke\"]')" in sql
    assert "status TEXT NOT NULL DEFAULT ('new')" in sql
    assert "runtime TEXT NOT NULL DEFAULT ('onnx-fp32')" in sql


def test_json_and_legacy_default_fields_remain_text_in_metadata():
    """Catches type drift from the existing legacy SQLite schema."""
    columns = (
        Camera.__table__.c.roi,
        Camera.__table__.c.detect_classes,
        DetectionEvent.__table__.c.status,
        ModelVersion.__table__.c.runtime,
    )

    assert all(isinstance(column.type, Text) for column in columns)


def test_mysql_version_older_than_expression_defaults_is_rejected():
    """Catches applying parenthesized Text defaults to unsupported MySQL releases."""
    engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql", server_version_info=(8, 0, 12)))

    with pytest.raises(RuntimeError, match=r"MySQL 8\.0\.13 or newer"):
        migrate.validate_mysql_version(engine)


def test_mysql_version_supporting_expression_defaults_is_accepted():
    """Catches rejecting the minimum MySQL version that supports expression defaults."""
    engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql", server_version_info=(8, 0, 13)))

    migrate.validate_mysql_version(engine)


def test_timestamp_columns_normalize_aware_values_and_reject_naive_values():
    """Catches accepting local/ambiguous datetimes for stored session expiry data."""
    processor = UserSession.__table__.c.expires_at.type.bind_processor(sqlite.dialect())
    assert processor is not None
    east_eight = timezone(timedelta(hours=8))

    assert processor(datetime(2030, 1, 2, 9, 4, 5, tzinfo=east_eight)) == datetime(2030, 1, 2, 1, 4, 5)
    with pytest.raises(ValueError, match="timezone-aware"):
        processor(datetime(2030, 1, 2, 1, 4, 5))


def test_mysql_engine_initializes_connections_in_utc():
    """Catches a MySQL engine configuration that leaves CURRENT_TIMESTAMP session-local."""
    engine = create_engine_from_url("mysql+pymysql://safety:safety@127.0.0.1/safety_test?charset=utf8mb4")
    try:
        _, keyword_arguments = engine.dialect.create_connect_args(engine.url)
        assert keyword_arguments["init_command"] == "SET time_zone = '+00:00'"
    finally:
        engine.dispose()


def test_mysql_migration_connections_initialize_in_utc():
    """Catches Alembic bypassing the engine helper for MySQL timestamp defaults."""
    config = alembic_config("mysql+pymysql://safety:safety@127.0.0.1/safety_test?charset=utf8mb4")

    _, keyword_arguments = mysql.dialect().create_connect_args(make_url(config.get_main_option("sqlalchemy.url")))
    assert keyword_arguments["init_command"] == "SET time_zone = '+00:00'"


def test_detection_event_metadata_retains_legacy_composite_index():
    """Catches metadata drift that would make Alembic remove the legacy event query index."""
    index_columns = {tuple(column.name for column in index.columns) for index in DetectionEvent.__table__.indexes}
    assert ("camera_id", "created_at") in index_columns


@pytest.mark.mysql
def test_mysql_upgrade_and_crud(mysql_url):
    """Catches a migration or engine option that only works on SQLite."""
    run_migrations(mysql_url)
    factory = create_session_factory(create_engine_from_url(mysql_url))
    with factory.begin() as session:
        session.add(Camera(name="mysql-camera", rtsp_url="rtsp://invalid", enabled=False))
    with factory() as session:
        assert session.scalar(select(Camera).where(Camera.name == "mysql-camera")) is not None
