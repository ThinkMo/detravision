from backend.db import Database


def test_camera_crud_and_json_round_trip(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    camera = db.create(
        "camera",
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
    assert db.update("camera", camera["id"], {"enabled": False})["enabled"] is False
    assert db.delete("camera", camera["id"])
    assert db.get("camera", camera["id"]) is None


def test_only_one_model_is_active(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    first = db.create("model_version", {"name": "a", "file_path": "a.onnx", "classes": ["fire"]})
    second = db.create("model_version", {"name": "b", "file_path": "b.onnx", "classes": ["fire"]})
    db.activate_model(first["id"])
    db.activate_model(second["id"])
    active = db.list("model_version", where="is_active = 1")
    assert [item["id"] for item in active] == [second["id"]]
