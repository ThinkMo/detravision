from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI

from backend import main as main_module
from backend.core.config import Settings


@pytest.fixture
def app_factory(tmp_path: Path) -> Callable[..., FastAPI]:
    """Build an isolated application without mutating module-global settings."""

    def build(*, database_url: str, **overrides: object) -> FastAPI:
        factory = getattr(main_module, "create_app", None)
        assert callable(factory), "backend.main.create_app is required for isolated startup"
        values: dict[str, object] = {
            "environment": "development",
            "database_url": database_url,
            "session_ttl_hours": 12,
            "admin_initial_password": None,
            "upload_dir": tmp_path / "uploads",
            "cookie_secure": False,
            "snapshot_dir": tmp_path / "snapshots",
            "model_path": None,
            "worker_count": 1,
            "queue_size": 8,
            "reconcile_interval": 3,
            "enable_runtime": False,
            "feishu_webhook": None,
        }
        values.update(overrides)
        config = Settings(**values)
        return factory(config)

    return build
