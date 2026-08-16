from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


def test_dashboard_summary_reports_current_counts_and_recent_events(app_factory, tmp_path):
    """Catches the dashboard returning placeholders or stale aggregate counts."""
    database_url = "sqlite:///" + (tmp_path / "dashboard.db").as_posix()
    app = app_factory(database_url=database_url)

    with TestClient(app) as client:
        assert client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123456"},
        ).status_code == 200
        assert client.post(
            "/api/auth/change-password",
            json={
                "current_password": "admin123456",
                "new_password": "Dashboard-password-123",
            },
        ).status_code == 204
        assert client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Dashboard-password-123"},
        ).status_code == 200

        repository = app.state.repository
        active = repository.create_camera(
            {
                "name": "东门",
                "rtsp_url": "rtsp://camera.local/one",
                "enabled": True,
                "sample_fps": 1.0,
                "roi": [],
                "detect_classes": ["no_helmet"],
                "alarm_group_id": None,
            }
        )
        repository.create_camera(
            {
                "name": "西门",
                "rtsp_url": "rtsp://camera.local/two",
                "enabled": False,
                "sample_fps": 1.0,
                "roi": [],
                "detect_classes": ["no_helmet"],
                "alarm_group_id": None,
            }
        )
        repository.create_event(
            {
                "camera_id": active["id"],
                "event_type": "no_helmet",
                "confidence": 0.91,
                "bbox": [1, 2, 3, 4],
                "snapshot_path": None,
                "status": "new",
            }
        )
        repository.create_event(
            {
                "camera_id": active["id"],
                "event_type": "no_helmet",
                "confidence": 0.88,
                "bbox": [5, 6, 7, 8],
                "snapshot_path": None,
                "status": "new",
                "created_at": datetime.now(timezone.utc) - timedelta(days=2),
            }
        )

        response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["camera_total"] == 2
    assert body["camera_active"] == 1
    assert body["events_today"] == 1
    assert body["events_new"] == 2
    assert len(body["recent_events"]) == 2
    assert body["recent_events"][0]["camera_id"] == active["id"]
    assert body["runtime"]["enabled"] is False
