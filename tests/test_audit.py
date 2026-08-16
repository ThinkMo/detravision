from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.audit import AuditService
from backend.auth import AuthService, hash_password
from backend.core.config import Settings
from backend.db import Repository
from backend.db.migrate import run_migrations
from backend.db.models import AuditLog, User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.main import app
from backend.ws import EventHub


@pytest.fixture
def audit_app(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "audit.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)
    repository = Repository(session_factory)
    auth_service = AuthService(
        session_factory,
        Settings(database_url=database_url, cookie_secure=False, enable_runtime=False),
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
            ]
        )

    app.state.auth_service = auth_service
    app.state.session_factory = session_factory
    app.state.repository = repository
    app.state.cookie_secure = False
    app.state.hub = EventHub()
    try:
        yield app, auth_service, session_factory, repository
    finally:
        engine.dispose()


def authenticated_client(audit_app, role: str) -> TestClient:
    api_app, auth_service, _, _ = audit_app
    credentials = {
        "admin": ("administrator", "Admin-password-123"),
        "user": ("operator", "Operator-password-123"),
    }
    username, password = credentials[role]
    result = auth_service.login(username, password, "127.0.0.1")
    client = TestClient(api_app)
    client.cookies.set("session", result.token)
    return client


def audit_rows(audit_app) -> list[AuditLog]:
    _, _, session_factory, _ = audit_app
    with session_factory() as session:
        return list(session.scalars(select(AuditLog).order_by(AuditLog.id)))


def test_login_success_and_failure_are_audited_without_passwords(audit_app):
    """Catches auth outcomes disappearing from audit or retaining submitted secrets."""
    api_app, _, _, _ = audit_app
    submitted_password = "Admin-password-123"
    rejected_password = "definitely-wrong-secret"
    client = TestClient(api_app)
    try:
        assert client.post(
            "/api/auth/login",
            json={"username": "administrator", "password": submitted_password},
        ).status_code == 200
    finally:
        client.close()
    client = TestClient(api_app)
    try:
        assert client.post(
            "/api/auth/login",
            json={"username": "administrator", "password": rejected_password},
        ).status_code == 401
    finally:
        client.close()

    rows = [row for row in audit_rows(audit_app) if row.action == "login"]
    assert [(row.actor_id, row.outcome) for row in rows] == [
        (1, "success"),
        (1, "rejected"),
    ]
    serialized = " ".join(row.details or "" for row in rows)
    assert submitted_password not in serialized
    assert rejected_password not in serialized


def test_successful_and_rejected_camera_mutations_are_audited(audit_app):
    """Catches either role denial or successful business writes bypassing the trail."""
    secret_url = "rtsp://camera-user:camera-password@example.invalid/private"
    payload = {
        "name": "loading bay",
        "rtsp_url": secret_url,
        "enabled": True,
        "sample_fps": 1,
        "roi": [],
        "detect_classes": ["fire"],
        "alarm_group_id": None,
    }
    client = authenticated_client(audit_app, "user")
    try:
        assert client.post("/api/cameras", json=payload).status_code == 403
    finally:
        client.close()
    client = authenticated_client(audit_app, "admin")
    try:
        created = client.post("/api/cameras", json=payload)
        assert created.status_code == 201
    finally:
        client.close()

    rows = [row for row in audit_rows(audit_app) if row.resource_type == "camera"]
    assert [(row.actor_id, row.action, row.outcome) for row in rows] == [
        (2, "create", "rejected"),
        (1, "create", "success"),
    ]
    assert rows[1].resource_id == str(created.json()["id"])
    assert secret_url not in " ".join(row.details or "" for row in rows)
    assert "camera-password" not in " ".join(row.details or "" for row in rows)


def test_not_found_and_conflicting_business_mutations_are_audited(audit_app):
    """Catches persistence/business rejections exiting before the audit trail."""
    client = authenticated_client(audit_app, "admin")
    try:
        assert client.patch("/api/cameras/999", json={"enabled": False}).status_code == 404
        assert client.post(
            "/api/alarm-groups",
            json={"name": "duplicate-group", "person_ids": []},
        ).status_code == 201
        assert client.post(
            "/api/alarm-groups",
            json={"name": "duplicate-group", "person_ids": []},
        ).status_code == 409
    finally:
        client.close()

    rejected = [row for row in audit_rows(audit_app) if row.outcome == "rejected"]
    assert [
        (row.action, row.resource_type, row.resource_id)
        for row in rejected
    ] == [
        ("update", "camera", "999"),
        ("create", "alarm_group", None),
    ]


def test_audit_service_redacts_nested_secrets_urls_and_local_paths(audit_app):
    """Catches removal or bypass of recursive audit-detail sanitization."""
    _, _, session_factory, _ = audit_app
    AuditService(session_factory).record(
        1,
        "update",
        "camera",
        7,
        "success",
        "127.0.0.1",
        details={
            "password": "submitted-password",
            "nested": {
                "token": "raw-session-token",
                "stream": "rtsp://camera-user:camera-password@example.invalid/live",
                "windows_location": "C:\\private\\model.onnx",
                "posix_location": "/srv/private/model.onnx",
                "status": "ack",
            },
        },
    )

    row = audit_rows(audit_app)[-1]
    details = json.loads(row.details or "{}")
    assert details == {
        "nested": {
            "posix_location": "[REDACTED]",
            "status": "ack",
            "stream": "[REDACTED]",
            "token": "[REDACTED]",
            "windows_location": "[REDACTED]",
        },
        "password": "[REDACTED]",
    }


def test_event_status_and_model_activation_audit_authenticated_actor(audit_app):
    """Catches high-value event/model state transitions losing actor attribution."""
    _, _, _, repository = audit_app
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
    event = repository.create_event(
        {
            "camera_id": camera["id"],
            "event_type": "fire",
            "confidence": 0.9,
            "bbox": [1, 2, 3, 4],
            "status": "new",
        }
    )
    client = authenticated_client(audit_app, "admin")
    try:
        model = client.post(
            "/api/models",
            json={
                "name": "fire-v2",
                "file_path": "C:\\private\\models\\fire-v2.onnx",
                "runtime": "onnx-fp32",
                "classes": ["fire"],
                "map50": 0.9,
            },
        )
        assert model.status_code == 201
        assert client.post(f"/api/models/{model.json()['id']}/activate").status_code == 200
    finally:
        client.close()
    client = authenticated_client(audit_app, "user")
    try:
        assert client.patch(
            f"/api/events/{event['id']}",
            json={"status": "ack"},
        ).status_code == 200
    finally:
        client.close()

    rows = audit_rows(audit_app)
    assert any(
        row.actor_id == 1
        and row.action == "activate"
        and row.resource_type == "model"
        and row.resource_id == str(model.json()["id"])
        and row.outcome == "success"
        for row in rows
    )
    assert any(
        row.actor_id == 2
        and row.action == "update"
        and row.resource_type == "event"
        and row.resource_id == str(event["id"])
        and row.outcome == "success"
        for row in rows
    )
    assert "C:\\private\\models\\fire-v2.onnx" not in " ".join(
        row.details or "" for row in rows
    )


def test_logout_password_change_and_user_management_are_audited(audit_app):
    """Catches account/session mutations completing without durable audit rows."""
    client = authenticated_client(audit_app, "user")
    try:
        assert client.post("/api/auth/logout").status_code == 204
    finally:
        client.close()
    client = authenticated_client(audit_app, "user")
    try:
        assert client.post(
            "/api/auth/change-password",
            json={
                "current_password": "Operator-password-123",
                "new_password": "Operator-password-456",
            },
        ).status_code == 204
    finally:
        client.close()
    client = authenticated_client(audit_app, "admin")
    try:
        created = client.post(
            "/api/users",
            json={
                "username": "audited.user",
                "password": "Audited-password-123",
                "role": "user",
            },
        )
        assert created.status_code == 201
        user_id = created.json()["id"]
        assert client.patch(f"/api/users/{user_id}", json={"enabled": False}).status_code == 200
        reset = client.post(f"/api/users/{user_id}/reset-password")
        assert reset.status_code == 200
        temporary_password = reset.json()["temporary_password"]
    finally:
        client.close()

    rows = audit_rows(audit_app)
    observed = {
        (row.action, row.resource_type, row.resource_id, row.outcome)
        for row in rows
    }
    assert ("logout", "session", "2", "success") in observed
    assert ("password_change", "user", "2", "success") in observed
    assert ("create", "user", str(user_id), "success") in observed
    assert ("update", "user", str(user_id), "success") in observed
    assert ("password_reset", "user", str(user_id), "success") in observed
    details = " ".join(row.details or "" for row in rows)
    assert "Audited-password-123" not in details
    assert temporary_password not in details


def test_audit_log_endpoint_is_admin_only_and_returns_sanitized_rows(audit_app):
    """Catches audit history being unavailable to admins or exposed to ordinary users."""
    api_app, _, _, _ = audit_app
    login_client = TestClient(api_app)
    try:
        assert login_client.post(
            "/api/auth/login",
            json={"username": "administrator", "password": "Admin-password-123"},
        ).status_code == 200
    finally:
        login_client.close()
    client = authenticated_client(audit_app, "user")
    try:
        assert client.get("/api/audit-logs").status_code == 403
    finally:
        client.close()
    client = authenticated_client(audit_app, "admin")
    try:
        response = client.get("/api/audit-logs")
        assert response.status_code == 200
        assert response.json()
        row = response.json()[0]
        assert set(row) == {
            "id",
            "actor_id",
            "action",
            "resource_type",
            "resource_id",
            "outcome",
            "details",
            "ip",
            "created_at",
        }
        if row["details"] is not None:
            json.loads(row["details"])
    finally:
        client.close()
