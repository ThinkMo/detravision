from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect
from sqlalchemy.engine import make_url

from .session import create_engine_from_url


LEGACY_TABLES = {"camera", "person", "alarm_group", "alarm_group_member", "detection_event", "model_version"}

LEGACY_COLUMNS = {
    "camera": {"id", "name", "rtsp_url", "enabled", "sample_fps", "roi", "detect_classes", "alarm_group_id", "created_at"},
    "person": {"id", "name", "phone", "feishu_id", "dept", "created_at"},
    "alarm_group": {"id", "name", "created_at"},
    "alarm_group_member": {"group_id", "person_id"},
    "detection_event": {"id", "camera_id", "event_type", "confidence", "bbox", "snapshot_path", "status", "ack_by", "created_at"},
    "model_version": {"id", "name", "file_path", "runtime", "classes", "map50", "is_active", "created_at"},
}


def validate_legacy_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    for table, required_columns in LEGACY_COLUMNS.items():
        existing_columns = {column["name"] for column in inspector.get_columns(table)}
        missing = sorted(required_columns - existing_columns)
        if missing:
            raise RuntimeError(f"Legacy table {table} is missing columns: {', '.join(missing)}")


def validate_mysql_version(engine: Engine) -> None:
    if engine.dialect.name != "mysql":
        return
    version = engine.dialect.server_version_info
    if version is None or version < (8, 0, 13):
        raise RuntimeError("MySQL 8.0.13 or newer is required for Text default expressions")


def alembic_config(database_url: str) -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() == "mysql":
        database_url = str(parsed_url.update_query_dict({"init_command": "SET time_zone = '+00:00'"}))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _ensure_sqlite_parent(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


def run_migrations(database_url: str) -> None:
    """Upgrade an empty or legacy database to the current Alembic head."""
    _ensure_sqlite_parent(database_url)
    engine = create_engine_from_url(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        validate_mysql_version(engine)
        config = alembic_config(database_url)
        if "alembic_version" not in tables and LEGACY_TABLES <= tables:
            validate_legacy_columns(engine)
            command.stamp(config, "0001")
        command.upgrade(config, "head")
    finally:
        engine.dispose()
