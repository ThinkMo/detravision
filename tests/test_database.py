import os

import pytest

from backend.db import Repository
from backend.db.migrate import run_migrations
from backend.db.session import create_engine_from_url, create_session_factory


@pytest.fixture
def repository(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "test.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    try:
        yield Repository(create_session_factory(engine))
    finally:
        engine.dispose()


def test_camera_crud_and_json_round_trip(repository):
    """Catches camera persistence losing JSON values or CRUD state changes."""
    camera = repository.create_camera(
        {
            "name": "gate",
            "rtsp_url": "rtsp://example.invalid/live",
            "enabled": True,
            "sample_fps": 2,
            "roi": [[0, 0], [100, 0], [100, 100]],
            "detect_classes": ["fire"],
            "alarm_group_id": None,
        },
    )
    assert camera["enabled"] is True
    assert camera["detect_classes"] == ["fire"]
    assert repository.update_camera(camera["id"], {"enabled": False})["enabled"] is False
    event = repository.create_event(
        {
            "camera_id": camera["id"],
            "event_type": "fire",
            "confidence": 0.95,
            "bbox": [1, 2, 3, 4],
            "status": "new",
        }
    )
    assert event["bbox"] == [1, 2, 3, 4]
    assert event["ack_by"] is None
    assert event["ack_user_id"] is None
    assert repository.delete_camera(camera["id"])
    assert repository.get_camera(camera["id"]) is None


def test_only_one_model_is_active(repository):
    """Catches model activation leaving a previously active model enabled."""
    first = repository.create_model({"name": "a", "file_path": "a.onnx", "classes": ["fire"]})
    second = repository.create_model({"name": "b", "file_path": "b.onnx", "classes": ["fire"]})
    repository.activate_model(first["id"])
    repository.activate_model(second["id"])
    active = [model for model in repository.list_models() if model["is_active"]]
    assert [item["id"] for item in active] == [second["id"]]


@pytest.mark.mysql
def test_mysql_repository_crud_parity():
    """Catches repository JSON/CRUD behavior that differs between SQLite and MySQL."""
    database_url = os.getenv("TEST_MYSQL_URL")
    if database_url is None:
        pytest.skip("TEST_MYSQL_URL is not configured")

    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    try:
        repository = Repository(create_session_factory(engine))
        camera = repository.create_camera(
            {
                "name": "mysql-parity-camera",
                "rtsp_url": "rtsp://example.invalid/mysql",
                "enabled": True,
                "sample_fps": 2,
                "roi": [[0, 0], [100, 0], [100, 100]],
                "detect_classes": ["fire"],
                "alarm_group_id": None,
            }
        )
        assert repository.update_camera(camera["id"], {"enabled": False})["enabled"] is False
        assert repository.get_camera(camera["id"])["roi"] == [[0, 0], [100, 0], [100, 100]]
        assert repository.delete_camera(camera["id"])
    finally:
        engine.dispose()
