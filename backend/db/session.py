from __future__ import annotations

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def create_engine_from_url(url: str) -> Engine:
    """Create an engine that enforces foreign keys on SQLite and works on MySQL."""
    options: dict[str, object] = {"future": True}
    parsed_url = make_url(url)
    if parsed_url.get_backend_name() == "sqlite":
        options["connect_args"] = {"check_same_thread": False}
    elif parsed_url.get_backend_name() == "mysql":
        parsed_url = parsed_url.update_query_dict({"init_command": "SET time_zone = '+00:00'"})
    engine = create_engine(parsed_url, **options)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
