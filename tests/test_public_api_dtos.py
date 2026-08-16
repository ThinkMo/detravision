from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient


def login_admin(client: TestClient) -> None:
    assert client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123456"},
    ).status_code == 200
    assert client.post(
        "/api/auth/change-password",
        json={
            "current_password": "admin123456",
            "new_password": "Public-dto-password-123",
        },
    ).status_code == 204
    assert client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Public-dto-password-123"},
    ).status_code == 200


def assert_no_storage_secrets(response, *sentinels: str) -> None:
    assert response.status_code < 400
    for sentinel in sentinels:
        assert sentinel not in response.text
    assert "rtsp_url" not in response.text
    assert "file_path" not in response.text
    assert "snapshot_path" not in response.text


def test_business_dtos_never_expose_rtsp_model_or_snapshot_storage(app_factory, tmp_path):
    """Catches persistence-only secrets crossing any admin or ordinary-user API response."""
    app = app_factory(database_url="sqlite:///" + (tmp_path / "public-dtos.db").as_posix())
    rtsp_secret = "rtsp://operator:sentinel-password@camera.invalid/private"
    model_secret = "C:\\private-models\\sentinel-model.onnx"
    snapshot_secret = "C:\\private-snapshots\\sentinel-event.jpg"

    with TestClient(app) as client:
        login_admin(client)
        camera_response = client.post(
            "/api/cameras",
            json={
                "name": "东门",
                "rtsp_url": rtsp_secret,
                "enabled": True,
                "sample_fps": 1,
                "roi": [],
                "detect_classes": ["no_helmet"],
                "alarm_group_id": None,
            },
        )
        assert_no_storage_secrets(camera_response, rtsp_secret)
        assert camera_response.json()["rtsp_configured"] is True
        camera_id = camera_response.json()["id"]
        replaced_rtsp = "rtsp://replacement:sentinel-two@camera.invalid/private"
        camera_update = client.patch(
            f"/api/cameras/{camera_id}",
            json={"rtsp_url": replaced_rtsp},
        )
        assert_no_storage_secrets(camera_update, rtsp_secret, replaced_rtsp)
        assert camera_update.json()["rtsp_configured"] is True

        model_response = client.post(
            "/api/models",
            json={
                "name": "helmet-safe-v1",
                "file_path": model_secret,
                "runtime": "onnx-fp32",
                "classes": ["helmet", "no_helmet"],
                "map50": 0.9,
            },
        )
        assert_no_storage_secrets(model_response, model_secret)
        model_activation = client.post(
            f"/api/models/{model_response.json()['id']}/activate"
        )
        assert_no_storage_secrets(model_activation, model_secret)

        event = app.state.repository.create_event(
            {
                "camera_id": camera_id,
                "event_type": "no_helmet",
                "confidence": 0.91,
                "bbox": [1, 2, 3, 4],
                "snapshot_path": snapshot_secret,
                "status": "new",
                "created_at": datetime.now(timezone.utc),
            }
        )

        admin_responses = [
            client.get("/api/cameras"),
            client.get("/api/models"),
            client.get("/api/events"),
            client.patch(f"/api/events/{event['id']}", json={"status": "ack"}),
            client.get("/api/dashboard/summary"),
        ]
        for response in admin_responses:
            assert_no_storage_secrets(
                response,
                rtsp_secret,
                replaced_rtsp,
                model_secret,
                snapshot_secret,
            )
        listed_event = admin_responses[2].json()[0]
        assert listed_event["snapshot_url"] == f"/api/events/{event['id']}/snapshot"

        assert client.post(
            "/api/users",
            json={
                "username": "dto.operator",
                "password": "Operator-password-123",
                "role": "user",
            },
        ).status_code == 201
        assert client.post("/api/auth/logout").status_code == 204
        assert client.post(
            "/api/auth/login",
            json={"username": "dto.operator", "password": "Operator-password-123"},
        ).status_code == 200
        assert client.post(
            "/api/auth/change-password",
            json={
                "current_password": "Operator-password-123",
                "new_password": "Operator-password-456",
            },
        ).status_code == 204
        assert client.post(
            "/api/auth/login",
            json={"username": "dto.operator", "password": "Operator-password-456"},
        ).status_code == 200
        for path in ("/api/cameras", "/api/models", "/api/events", "/api/dashboard/summary"):
            assert_no_storage_secrets(
                client.get(path),
                rtsp_secret,
                replaced_rtsp,
                model_secret,
                snapshot_secret,
            )
