from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient


def _login(client: TestClient) -> None:
    assert client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123456"},
    ).status_code == 200
    assert client.post(
        "/api/auth/change-password",
        json={
            "current_password": "admin123456",
            "new_password": "Event-center-password-123",
        },
    ).status_code == 204
    assert client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Event-center-password-123"},
    ).status_code == 200


def _camera(repository):
    return repository.create_camera(
        {
            "name": "East gate",
            "rtsp_url": "rtsp://camera.invalid/live",
            "enabled": True,
            "sample_fps": 1.0,
            "roi": [],
            "detect_classes": ["no_helmet"],
            "alarm_group_id": None,
        }
    )


def _event(repository, camera_id: int, created_at: datetime, snapshot_path: str | None = None):
    return repository.create_event(
        {
            "camera_id": camera_id,
            "event_type": "no_helmet",
            "confidence": 0.91,
            "bbox": [1, 2, 3, 4],
            "snapshot_path": snapshot_path,
            "status": "new",
            "created_at": created_at,
        }
    )


def test_events_filter_by_explicit_utc_range(app_factory, tmp_path):
    """Catches either UTC boundary being ignored by the event query."""
    app = app_factory(database_url="sqlite:///" + (tmp_path / "events.db").as_posix())

    with TestClient(app) as client:
        _login(client)
        repository = app.state.repository
        camera = _camera(repository)
        _event(repository, camera["id"], datetime(2026, 8, 14, 23, 59, tzinfo=timezone.utc))
        included = _event(repository, camera["id"], datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc))
        _event(repository, camera["id"], datetime(2026, 8, 16, 0, 1, tzinfo=timezone.utc))

        response = client.get(
            "/api/events",
            params={
                "created_from": "2026-08-15T00:00:00Z",
                "created_to": "2026-08-16T00:00:00Z",
            },
        )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [included["id"]]


def test_snapshot_serves_only_supported_images_below_configured_root(app_factory, tmp_path, monkeypatch):
    """Catches the media endpoint returning arbitrary or out-of-root files."""
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    image = snapshot_root / "event.jpg"
    image.write_bytes(b"\xff\xd8\xff\xd9")
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"secret")
    text_file = snapshot_root / "event.txt"
    text_file.write_text("not an image", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    app = app_factory(
        database_url="sqlite:///" + (tmp_path / "snapshots.db").as_posix(),
        snapshot_dir="snapshots",
    )

    with TestClient(app) as client:
        _login(client)
        repository = app.state.repository
        camera = _camera(repository)
        valid = _event(repository, camera["id"], datetime.now(timezone.utc), "event.jpg")
        legacy_relative = _event(repository, camera["id"], datetime.now(timezone.utc), "snapshots/event.jpg")
        escaped = _event(repository, camera["id"], datetime.now(timezone.utc), "../secret.jpg")
        unsupported = _event(repository, camera["id"], datetime.now(timezone.utc), "event.txt")

        served = client.get(f"/api/events/{valid['id']}/snapshot")
        served_legacy = client.get(f"/api/events/{legacy_relative['id']}/snapshot")
        escaped_response = client.get(f"/api/events/{escaped['id']}/snapshot")
        unsupported_response = client.get(f"/api/events/{unsupported['id']}/snapshot")

    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert "content-disposition" not in served.headers
    assert served.content == b"\xff\xd8\xff\xd9"
    assert served_legacy.status_code == 200
    assert escaped_response.status_code == 404
    assert unsupported_response.status_code == 404
