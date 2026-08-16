import asyncio

import httpx
import pytest
from fastapi import FastAPI

from backend.api import routes
from backend.auth import AuthenticatedUser
from backend.auth.dependencies import current_user
from backend.db import Repository
from backend.db.migrate import run_migrations
from backend.db.models import User
from backend.db.session import create_engine_from_url, create_session_factory


@pytest.fixture
def api_app(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "api.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        session.add(
            User(
                username="compatibility-admin",
                password_hash="unused-by-dependency-override",
                role="admin",
                enabled=True,
                must_change_password=False,
            )
        )
    app = FastAPI()
    app.include_router(routes.router)
    app.state.repository = Repository(session_factory)
    app.state.session_factory = session_factory
    app.dependency_overrides[current_user] = lambda: AuthenticatedUser(
        id=1,
        username="compatibility-admin",
        role="admin",
        must_change_password=False,
    )
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def request(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_camera_routes_preserve_response_statuses_and_keys(api_app):
    """Catches ORM migration changing the established camera HTTP contract."""
    listed = request(api_app, "GET", "/api/cameras")
    assert listed.status_code == 200
    assert listed.json() == []

    created = request(
        api_app,
        "POST",
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

    updated = request(api_app, "PATCH", f"/api/cameras/{camera['id']}", json={"enabled": False})
    assert updated.status_code == 200
    assert set(updated.json()) == set(camera)
    assert updated.json()["enabled"] is False

    deleted = request(api_app, "DELETE", f"/api/cameras/{camera['id']}")
    assert deleted.status_code == 204
    assert deleted.content == b""


def test_person_routes_preserve_response_statuses_and_keys(api_app):
    """Catches ORM migration changing the established person HTTP contract."""
    listed = request(api_app, "GET", "/api/persons")
    assert listed.status_code == 200
    assert listed.json() == []

    created = request(
        api_app,
        "POST",
        "/api/persons",
        json={"name": "Ava", "phone": "123", "feishu_id": "ava", "dept": "Safety"},
    )
    assert created.status_code == 201
    person = created.json()
    assert set(person) == {"id", "name", "phone", "feishu_id", "dept", "created_at"}

    updated = request(api_app, "PATCH", f"/api/persons/{person['id']}", json={"dept": "Operations"})
    assert updated.status_code == 200
    assert set(updated.json()) == set(person)
    assert updated.json()["dept"] == "Operations"

    deleted = request(api_app, "DELETE", f"/api/persons/{person['id']}")
    assert deleted.status_code == 204
    assert deleted.content == b""


def test_alarm_group_routes_allow_empty_members_and_preserve_response_keys(api_app):
    """Catches empty membership replacement becoming an invalid SQL insert."""
    listed = request(api_app, "GET", "/api/alarm-groups")
    assert listed.status_code == 200
    assert listed.json() == []

    created = request(api_app, "POST", "/api/alarm-groups", json={"name": "night-shift"})
    assert created.status_code == 201
    group = created.json()
    assert set(group) == {"id", "name", "created_at", "members"}
    assert group["members"] == []

    listed = request(api_app, "GET", "/api/alarm-groups")
    assert listed.status_code == 200
    assert listed.json() == [group]


def test_event_routes_preserve_response_statuses_and_keys(api_app):
    """Catches ORM event conversion dropping acknowledgement fields at the API boundary."""
    camera = api_app.state.repository.create_camera(
        {
            "name": "gate",
            "rtsp_url": "rtsp://example.invalid/live",
            "enabled": True,
            "sample_fps": 1,
            "roi": [],
            "detect_classes": ["fire"],
            "alarm_group_id": None,
        }
    )
    event = api_app.state.repository.create_event(
        {
            "camera_id": camera["id"],
            "event_type": "fire",
            "confidence": 0.98,
            "bbox": [1, 2, 3, 4],
            "status": "new",
        }
    )

    listed = request(api_app, "GET", "/api/events")
    assert listed.status_code == 200
    assert listed.json() == [event]
    assert set(event) == {
        "id", "camera_id", "event_type", "confidence", "bbox", "snapshot_path", "status", "ack_by", "ack_user_id", "created_at"
    }

    updated = request(api_app, "PATCH", f"/api/events/{event['id']}", json={"status": "ack"})
    assert updated.status_code == 200
    assert set(updated.json()) == set(event)
    assert updated.json()["status"] == "ack"


def test_model_routes_preserve_response_statuses_and_keys(api_app):
    """Catches model activation or ORM conversion changing the existing HTTP contract."""
    listed = request(api_app, "GET", "/api/models")
    assert listed.status_code == 200
    assert listed.json() == []

    created = request(
        api_app,
        "POST",
        "/api/models",
        json={"name": "fire-v1", "file_path": "fire.onnx", "runtime": "onnx-fp32", "classes": ["fire"], "map50": 0.9},
    )
    assert created.status_code == 201
    model = created.json()
    assert set(model) == {"id", "name", "file_path", "runtime", "classes", "map50", "is_active", "created_at"}

    activated = request(api_app, "POST", f"/api/models/{model['id']}/activate")
    assert activated.status_code == 200
    assert set(activated.json()) == set(model)
    assert activated.json()["is_active"] is True
