from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS camera (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    rtsp_url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    sample_fps REAL NOT NULL DEFAULT 1.0,
    roi TEXT NOT NULL DEFAULT '[]',
    detect_classes TEXT NOT NULL DEFAULT '["no_helmet","fire","smoke"]',
    alarm_group_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS person (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT,
    feishu_id TEXT,
    dept TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS alarm_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS alarm_group_member (
    group_id INTEGER NOT NULL REFERENCES alarm_group(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, person_id)
);
CREATE TABLE IF NOT EXISTS detection_event (
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
CREATE TABLE IF NOT EXISTS model_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    runtime TEXT NOT NULL DEFAULT 'onnx-fp32',
    classes TEXT NOT NULL,
    map50 REAL,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_event_camera_created ON detection_event(camera_id, created_at DESC);
"""

JSON_FIELDS = {"roi", "detect_classes", "bbox", "classes"}
BOOL_FIELDS = {"enabled", "is_active"}


class Database:
    """Small SQLite repository used by the MVP.

    Every operation owns its connection, so API and capture threads can safely
    share this object. The API boundary intentionally stays database-agnostic.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._schema_lock = threading.Lock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._schema_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as connection:
                connection.executescript(SCHEMA)

    @staticmethod
    def _encode(data: dict[str, Any]) -> dict[str, Any]:
        encoded = {}
        for key, value in data.items():
            if key in JSON_FIELDS:
                encoded[key] = json.dumps(value, ensure_ascii=False)
            elif key in BOOL_FIELDS:
                encoded[key] = int(value)
            else:
                encoded[key] = value
        return encoded

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key in JSON_FIELDS & result.keys():
            result[key] = json.loads(result[key])
        for key in BOOL_FIELDS & result.keys():
            result[key] = bool(result[key])
        return result

    def list(self, table: str, *, where: str = "", params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        query = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "") + " ORDER BY id DESC"
        with self.connect() as connection:
            return [self._row(row) for row in connection.execute(query, tuple(params)).fetchall()]  # type: ignore[misc]

    def get(self, table: str, item_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            return self._row(connection.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone())

    def create(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        values = self._encode(data)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        with self.connect() as connection:
            cursor = connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values())
            )
            item_id = int(cursor.lastrowid)
        return self.get(table, item_id)  # type: ignore[return-value]

    def update(self, table: str, item_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        if not data:
            return self.get(table, item_id)
        values = self._encode(data)
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?", (*values.values(), item_id)
            )
            if cursor.rowcount == 0:
                return None
        return self.get(table, item_id)

    def delete(self, table: str, item_id: int) -> bool:
        with self.connect() as connection:
            return connection.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,)).rowcount > 0

    def replace_group_members(self, group_id: int, person_ids: list[int]) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM alarm_group_member WHERE group_id = ?", (group_id,))
            connection.executemany(
                "INSERT INTO alarm_group_member(group_id, person_id) VALUES (?, ?)",
                [(group_id, person_id) for person_id in person_ids],
            )

    def group_members(self, group_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT p.* FROM person p JOIN alarm_group_member m ON p.id=m.person_id WHERE m.group_id=?",
                (group_id,),
            ).fetchall()
        return [self._row(row) for row in rows]  # type: ignore[misc]

    def activate_model(self, item_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM model_version WHERE id=?", (item_id,)).fetchone() is None:
                return None
            connection.execute("UPDATE model_version SET is_active=0")
            connection.execute("UPDATE model_version SET is_active=1 WHERE id=?", (item_id,))
        return self.get("model_version", item_id)
