from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import routes
from backend.db import Repository
from backend.db.migrate import run_migrations
from backend.db.session import create_engine_from_url, create_session_factory


class RuntimeStub:
    def status(self):
        return {"enabled": False}


def test_camera_routes_preserve_response_statuses_and_keys(tmp_path):
    """Catches ORM migration changing the established camera HTTP contract."""
    database_url = "sqlite:///" + (tmp_path / "api.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    app = FastAPI()
    app.include_router(routes.router)
    app.state.repository = Repository(create_session_factory(engine))
    app.state.runtime = RuntimeStub()

    try:
        with TestClient(app) as client:
            assert client.get("/api/cameras").status_code == 200
            assert client.get("/api/cameras").json() == []

            created = client.post(
                "/api/cameras",
                json={
                    "name": "gate",
                    "rtsp_url": "rtsp://example.invalid/live",
                    "enabled": True,
                    "sample_fps": 2,
                    "roi": [[0, 0], [100, 0], [100, 100]],
                    "detect_classes": ["fire"],
                    "alarm_group_id": None,
                },
            )
            assert created.status_code == 201
            camera = created.json()
            assert set(camera) == {
                "id", "name", "rtsp_url", "enabled", "sample_fps", "roi", "detect_classes", "alarm_group_id", "created_at"
            }

            updated = client.patch(f"/api/cameras/{camera['id']}", json={"enabled": False})
            assert updated.status_code == 200
            assert set(updated.json()) == set(camera)
            assert updated.json()["enabled"] is False

            deleted = client.delete(f"/api/cameras/{camera['id']}")
            assert deleted.status_code == 204
            assert deleted.content == b""
    finally:
        engine.dispose()
