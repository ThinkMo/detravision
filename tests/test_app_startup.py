from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import inspect

from backend import main as main_module
from backend.db.migrate import run_migrations
from backend.db.session import create_engine_from_url


def test_empty_database_startup_bootstraps_and_serves_health(
    app_factory: Callable[..., FastAPI],
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    database_url = "sqlite:///" + database_path.as_posix()
    app = app_factory(database_url=database_url)

    async def exercise_startup() -> tuple[httpx.Response, httpx.Response]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                health = await client.get("/api/health")
                login = await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "admin123456"},
                )
                return health, login

    health, login = asyncio.run(exercise_startup())

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert login.status_code == 200
    assert login.json() == {
        "id": 1,
        "username": "admin",
        "role": "admin",
        "must_change_password": True,
    }

    engine = create_engine_from_url(database_url)
    try:
        assert {"alembic_version", "user", "site_settings", "audit_log"} <= set(
            inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()


def test_production_startup_uses_pre_applied_migrations(
    app_factory: Callable[..., FastAPI],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = "sqlite:///" + (tmp_path / "production.db").as_posix()
    run_migrations(database_url)

    def reject_automatic_migration(_database_url: str) -> None:
        raise AssertionError("production startup must not run migrations")

    monkeypatch.setattr(main_module, "run_migrations", reject_automatic_migration)
    app = app_factory(
        database_url=database_url,
        environment="production",
        admin_initial_password="Production-only-123",
    )

    async def exercise_startup() -> httpx.Response:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="https://testserver",
            ) as client:
                return await client.post(
                    "/api/auth/login",
                    json={
                        "username": "admin",
                        "password": "Production-only-123",
                    },
                )

    login = asyncio.run(exercise_startup())
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
