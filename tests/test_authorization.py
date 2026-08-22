from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from backend.auth import AuthService, hash_password
from backend.core.config import Settings
from backend.db import Repository
from backend.db.migrate import run_migrations
from backend.db.models import User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.main import app
from backend.ws import EventHub


@pytest.fixture
def protected_app(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "authorization.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)
    repository = Repository(session_factory)
    auth_service = AuthService(
        session_factory,
        Settings(
            database_url=database_url,
            admin_initial_password="Admin-password-123",
            cookie_secure=False,
            enable_runtime=False,
        ),
    )
    with session_factory.begin() as session:
        session.add_all(
            [
                User(
                    username="administrator",
                    password_hash=hash_password("Admin-password-123"),
                    role="admin",
                    enabled=True,
                    must_change_password=False,
                ),
                User(
                    username="operator",
                    password_hash=hash_password("Operator-password-123"),
                    role="user",
                    enabled=True,
                    must_change_password=False,
                ),
                User(
                    username="must-change",
                    password_hash=hash_password("Change-password-123"),
                    role="user",
                    enabled=True,
                    must_change_password=True,
                ),
                User(
                    username="disabled",
                    password_hash=hash_password("Disabled-password-123"),
                    role="user",
                    enabled=True,
                    must_change_password=False,
                ),
            ]
        )

    camera = repository.create_camera(
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
    person = repository.create_person(
        {
            "name": "Legacy responder",
            "phone": None,
            "feishu_id": None,
            "dept": None,
        }
    )
    event = repository.create_event(
        {
            "camera_id": camera["id"],
            "event_type": "fire",
            "confidence": 0.98,
            "bbox": [1, 2, 3, 4],
            "status": "new",
            "ack_by": person["id"],
        }
    )

    app.state.auth_service = auth_service
    app.state.session_factory = session_factory
    app.state.repository = repository
    app.state.cookie_secure = False
    app.state.hub = EventHub()
    app.state.runtime = SimpleNamespace(status=lambda: {"state": "stopped"})
    try:
        yield app, auth_service, session_factory, event
    finally:
        engine.dispose()


@pytest.fixture
def authenticated_client(protected_app) -> Callable[[str], TestClient]:
    api_app, auth_service, _, _ = protected_app
    credentials = {
        "admin": ("administrator", "Admin-password-123"),
        "user": ("operator", "Operator-password-123"),
    }
    clients: list[TestClient] = []

    def create(role: str) -> TestClient:
        username, password = credentials[role]
        token = auth_service.login(username, password, "127.0.0.1").token
        client = TestClient(api_app)
        client.cookies.set("session", token)
        clients.append(client)
        return client

    try:
        yield create
    finally:
        for client in clients:
            client.close()


def request_body(method: str, path: str) -> dict[str, object] | None:
    if method == "POST" and path == "/api/cameras":
        return {
            "name": "loading bay",
            "rtsp_url": "rtsp://example.invalid/loading-bay",
            "enabled": True,
            "sample_fps": 1,
            "roi": [],
            "detect_classes": ["fire"],
            "alarm_group_id": None,
        }
    if method == "POST" and path == "/api/models":
        return {
            "name": "fire-v2",
            "file_path": "fire-v2.onnx",
            "runtime": "onnx-fp32",
            "classes": ["fire"],
            "map50": 0.9,
        }
    if method == "PATCH" and path == "/api/events/1":
        return {"status": "ack", "ack_by": None}
    return None


@pytest.mark.parametrize(
    ("method", "path", "role", "expected"),
    [
        ("GET", "/api/cameras", "user", 200),
        ("POST", "/api/cameras", "user", 403),
        ("GET", "/api/models", "user", 200),
        ("GET", "/api/training/catalog", "user", 403),
        ("POST", "/api/models", "user", 403),
        ("PATCH", "/api/events/1", "user", 200),
        ("POST", "/api/cameras", "admin", 201),
        ("POST", "/api/models", "admin", 201),
    ],
)
def test_permission_matrix(
    authenticated_client,
    method: str,
    path: str,
    role: str,
    expected: int,
):
    """Catches legacy reads/writes using the wrong authenticated role boundary."""
    client = authenticated_client(role)
    response = client.request(method, path, json=request_body(method, path))
    assert response.status_code == expected


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/cameras"),
        ("GET", "/api/persons"),
        ("GET", "/api/alarm-groups"),
        ("GET", "/api/events"),
        ("GET", "/api/models"),
        ("GET", "/api/training/catalog"),
        ("GET", "/api/training-jobs"),
        ("DELETE", "/api/cameras/1"),
    ],
)
def test_legacy_routes_reject_requests_without_a_session(
    protected_app,
    method: str,
    path: str,
):
    """Catches a legacy business route remaining public after auth rollout."""
    api_app, _, _, _ = protected_app
    client = TestClient(api_app)
    try:
        assert client.request(method, path).status_code == 401
    finally:
        client.close()


def test_event_ack_attributes_authenticated_user_without_overwriting_legacy_person(
    authenticated_client,
):
    """Catches web acknowledgement mixing User IDs into the legacy Person reference."""
    client = authenticated_client("user")
    response = client.patch(
        "/api/events/1",
        json={"status": "closed", "ack_by": None},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "closed"
    assert response.json()["ack_by"] == 1
    assert response.json()["ack_user_id"] == 2


def test_websocket_without_cookie_closes_before_connecting_to_hub(protected_app):
    """Catches an unauthenticated WebSocket being accepted into the event hub."""
    api_app, _, _, _ = protected_app
    client = TestClient(api_app)
    try:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect("/ws/events"):
                pass
    finally:
        client.close()
    assert rejected.value.code == 4401
    assert api_app.state.hub._clients["events"] == set()


def test_disabled_user_websocket_closes_as_unauthenticated(protected_app):
    """Catches a disabled account retaining a usable live-stream handshake."""
    api_app, auth_service, _, _ = protected_app
    result = auth_service.login("disabled", "Disabled-password-123", "127.0.0.1")
    auth_service.set_user_enabled(result.user.id, False)
    client = TestClient(api_app)
    try:
        client.cookies.set("session", result.token)
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect("/ws/live/1"):
                pass
    finally:
        client.close()
    assert rejected.value.code == 4401
    assert api_app.state.hub._clients["live:1"] == set()


def test_forced_password_change_websocket_closes_with_policy_code(protected_app):
    """Catches a forced-change session reaching application WebSockets."""
    api_app, auth_service, _, _ = protected_app
    result = auth_service.login("must-change", "Change-password-123", "127.0.0.1")
    client = TestClient(api_app)
    try:
        client.cookies.set("session", result.token)
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect("/ws/events"):
                pass
    finally:
        client.close()
    assert rejected.value.code == 4403


@pytest.mark.parametrize("path", ["/ws/events", "/ws/live/1"])
def test_authenticated_websocket_connects_with_cookie_only(
    protected_app,
    path: str,
):
    """Catches valid cookie authentication being lost on either WebSocket surface."""
    api_app, auth_service, _, _ = protected_app
    token = auth_service.login("operator", "Operator-password-123", "127.0.0.1").token
    client = TestClient(api_app)
    try:
        client.cookies.set("session", token)
        with client.websocket_connect(path) as websocket:
            websocket.send_text("ping")
    finally:
        client.close()
