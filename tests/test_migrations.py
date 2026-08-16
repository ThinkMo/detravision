from __future__ import annotations

import os
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
from backend.db.models import Camera, DetectionEvent, ModelVersion, UserSession
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
    } <= names


def test_legacy_sqlite_is_stamped_then_upgraded(legacy_database_url):
    """Catches treating an existing MVP database as a fresh empty database."""
    run_migrations(legacy_database_url)

    with create_engine(legacy_database_url).connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0003"
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
