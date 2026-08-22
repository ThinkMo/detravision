from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from test_training_api import _app


class FakeAnnotations:
    def __init__(self, image_path: Path) -> None:
        self.image_path = image_path

    def get_image(self, dataset_id: int, image_id: int):
        return {
            "id": image_id, "dataset_id": dataset_id, "split": "test",
            "display_name": "new.jpg", "image_key": "new.jpg",
            "width": 80, "height": 60, "revision": 0,
            "review_status": "pending_annotation", "validation_code": None,
            "boxes": [],
        }

    def content(self, dataset_id: int, image_id: int):
        return self.image_path, "image/jpeg"

    def save(self, dataset_id: int, image_id: int, revision: int, boxes):
        return {**self.get_image(dataset_id, image_id), "revision": 1, "review_status": "annotated"}

    def confirm_empty(self, dataset_id: int, image_id: int, revision: int):
        return {**self.get_image(dataset_id, image_id), "revision": 1, "review_status": "confirmed_empty"}

    def add_class(self, dataset_id: int, revision: int, name: str):
        return {"id": dataset_id, "revision": revision + 1, "classes": ["person", name]}

    def rename_class(self, dataset_id: int, revision: int, class_id: int, name: str):
        return {"id": dataset_id, "revision": revision + 1, "classes": ["person", name]}

    def delete_class(self, dataset_id: int, revision: int, class_id: int):
        return {"id": dataset_id, "revision": revision + 1, "classes": ["person"]}


class FakeValidator:
    class Report:
        def to_json(self):
            return {"errors": [], "warnings": [], "counts": {}, "digest": "a" * 64}

    def validate(self, dataset_id: int):
        return self.Report()

    def publish(self, dataset_id: int, revision: int, confirm_warnings: bool):
        return {"id": dataset_id, "status": "published", "revision": revision + 1, "manifest_digest": "a" * 64}


def test_admin_can_complete_dataset_annotation_transport(tmp_path: Path):
    app, _training, _audit = _app(tmp_path)
    image = tmp_path / "new.jpg"
    image.write_bytes(b"jpeg")
    app.state.annotation_service = FakeAnnotations(image)
    app.state.dataset_validator = FakeValidator()
    dataset = app.state.training_dataset_service.item
    editing = {**dataset, "id": 4, "key": "dataset:4", "status": "editing", "version": 2, "revision": 0}
    service = app.state.training_dataset_service
    service.clone_version = lambda dataset_id, created_by: editing
    service.list_versions = lambda dataset_id: [dataset, editing]
    service.rescan = lambda dataset_id, split: {"discovered": 1, "changed": 0, "missing": 0, "total": 1}
    service.list_images = lambda dataset_id, **kwargs: {
        "items": [app.state.annotation_service.get_image(dataset_id, 9)],
        "total": 1, "page": 1, "page_size": 50,
    }
    headers = {"Origin": "http://testserver"}

    with TestClient(app) as client:
        clone = client.post("/api/training/datasets/3/versions", headers=headers)
        versions = client.get("/api/training/datasets/3/versions")
        rescan = client.post(
            "/api/training/datasets/4/rescan", json={"split": "test"}, headers=headers
        )
        images = client.get("/api/training/datasets/4/images?split=test")
        detail = client.get("/api/training/datasets/4/images/9")
        content = client.get("/api/training/datasets/4/images/9/content")
        saved = client.put(
            "/api/training/datasets/4/images/9/annotations",
            json={"revision": 0, "boxes": [{
                "class_id": 2, "x_center": 0.5, "y_center": 0.2,
                "width": 0.1, "height": 0.1,
            }]}, headers=headers,
        )
        checked = client.post("/api/training/datasets/4/validate", headers=headers)
        published = client.post(
            "/api/training/datasets/4/publish",
            json={"revision": 0, "confirm_warnings": True}, headers=headers,
        )

    assert clone.status_code == 201
    assert versions.status_code == rescan.status_code == images.status_code == 200
    assert detail.status_code == saved.status_code == checked.status_code == 200
    assert content.status_code == 200
    assert content.headers["content-disposition"].startswith("inline")
    assert content.headers["x-content-type-options"] == "nosniff"
    assert saved.json()["review_status"] == "annotated"
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert str(tmp_path) not in "".join(
        response.text for response in (clone, versions, rescan, images, detail, saved, checked, published)
    )


def test_regular_user_cannot_use_annotation_routes(tmp_path: Path):
    app, _training, _audit = _app(tmp_path, role="user")
    app.state.annotation_service = FakeAnnotations(tmp_path / "new.jpg")
    app.state.dataset_validator = FakeValidator()

    with TestClient(app) as client:
        response = client.get("/api/training/datasets/3/versions")

    assert response.status_code == 403
