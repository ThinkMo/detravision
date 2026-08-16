from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from backend import main as main_module
from backend.core.config import Settings


def _request(app, path: str) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path)

    return asyncio.run(send())


@pytest.fixture
def app_with_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dist_dir = tmp_path / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text(
        '<!doctype html><div id="root"></div>',
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("console.log('ok')", encoding="utf-8")
    monkeypatch.setattr(main_module, "FRONTEND_DIST_DIR", dist_dir, raising=False)
    return main_module.create_app(Settings(enable_runtime=False))


def test_root_and_browser_route_serve_frontend(app_with_dist) -> None:
    root = _request(app_with_dist, "/")
    browser_route = _request(app_with_dist, "/events/active")

    assert root.status_code == 200
    assert '<div id="root"></div>' in root.text
    assert browser_route.status_code == 200
    assert '<div id="root"></div>' in browser_route.text


def test_backend_and_documentation_routes_are_not_spa_fallback(app_with_dist) -> None:
    assert _request(app_with_dist, "/api/not-a-route").status_code == 404
    assert _request(app_with_dist, "/ws/not-a-route").status_code == 404
    assert _request(app_with_dist, "/docs").status_code == 200
    assert _request(app_with_dist, "/openapi.json").status_code == 200


def test_existing_and_missing_assets_do_not_fall_back_to_index(app_with_dist) -> None:
    existing = _request(app_with_dist, "/assets/app.js")
    missing = _request(app_with_dist, "/assets/missing.js")

    assert existing.status_code == 200
    assert existing.text == "console.log('ok')"
    assert missing.status_code == 404
    assert '<div id="root"></div>' not in missing.text


def test_missing_frontend_build_does_not_prevent_api_route_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "FRONTEND_DIST_DIR",
        tmp_path / "missing-dist",
        raising=False,
    )
    app = main_module.create_app(Settings(enable_runtime=False))

    assert _request(app, "/api/not-a-route").status_code == 404
    unavailable = _request(app, "/")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "frontend build is unavailable"}
