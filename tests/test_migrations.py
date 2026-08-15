from __future__ import annotations

import os
import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, select, text

from backend.db.migrate import run_migrations
from backend.db.models import Camera
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
    assert {"user", "user_session", "site_settings", "audit_log"} <= names


def test_legacy_sqlite_is_stamped_then_upgraded(legacy_database_url):
    """Catches treating an existing MVP database as a fresh empty database."""
    run_migrations(legacy_database_url)

    with create_engine(legacy_database_url).connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0002"
        event_columns = {column["name"] for column in inspect(connection).get_columns("detection_event")}
    assert {"ack_by", "ack_user_id"} <= event_columns


def test_incompatible_legacy_schema_is_rejected_before_stamping(tmp_path):
    """Catches accepting a partial legacy table and corrupting its upgrade path."""
    path = tmp_path / "incompatible.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(LEGACY_SCHEMA.replace("    rtsp_url TEXT NOT NULL,\n", ""))

    with pytest.raises(RuntimeError, match=r"camera.*rtsp_url"):
        run_migrations("sqlite:///" + path.as_posix())


@pytest.mark.mysql
def test_mysql_upgrade_and_crud(mysql_url):
    """Catches a migration or engine option that only works on SQLite."""
    run_migrations(mysql_url)
    factory = create_session_factory(create_engine_from_url(mysql_url))
    with factory.begin() as session:
        session.add(Camera(name="mysql-camera", rtsp_url="rtsp://invalid", enabled=False))
    with factory() as session:
        assert session.scalar(select(Camera).where(Camera.name == "mysql-camera")) is not None
