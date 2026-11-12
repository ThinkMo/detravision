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


def test_publication_failure_returns_actionable_validation_details(tmp_path: Path):
    from backend.training.dataset_validation import DatasetValidationError, DatasetValidationReport, ValidationIssue

    app, _training, _audit = _app(tmp_path)

    class BlockedValidator:
        def publish(self, dataset_id, revision, confirm_warnings):
            report = DatasetValidationReport(
                [ValidationIssue("annotation_pending", "error", split="train", image_id=9)],
                [], {"images": {"train": 1, "val": 0, "test": 0}}, None,
            )
            raise DatasetValidationError("dataset_validation_failed", report)

    app.state.dataset_validator = BlockedValidator()
    with TestClient(app) as client:
        response = client.post(
            "/api/training/datasets/4/publish",
            json={"revision": 0, "confirm_warnings": True},
            headers={"Origin": "http://testserver"},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "dataset_validation_failed"
    assert body["validation"]["errors"][0]["code"] == "annotation_pending"
    assert body["validation"]["errors"][0]["split"] == "train"
    assert body["validation"]["counts"]["images"]["train"] == 1
    assert str(tmp_path) not in response.text

def test_real_annotation_completion_counts_all_splits_before_publication(tmp_path: Path):
    from PIL import Image
    from backend.core.config import Settings
    from backend.db.migrate import run_migrations
    from backend.db.models import User
    from backend.db.session import create_engine_from_url, create_session_factory
    from backend.training.annotation_service import AnnotationService
    from backend.training.dataset_filesystem import DatasetFilesystem
    from backend.training.dataset_repository import TrainingDatasetRepository
    from backend.training.dataset_service import DatasetService
    from backend.training.dataset_validation import DatasetValidator

    root = tmp_path / "datasets"
    for split in ("train", "val", "test"):
        (root / "source" / "images" / split).mkdir(parents=True)
        (root / "source" / "labels" / split).mkdir(parents=True)
        for index in range(51 if split == "test" else 1):
            Image.new("RGB", (80, 60), "white").save(root / "source" / "images" / split / f"frame-{index:03}.jpg")
    url = "sqlite:///" + (tmp_path / "completion.db").as_posix()
    run_migrations(url)
    engine = create_engine_from_url(url)
    try:
        sessions = create_session_factory(engine)
        with sessions.begin() as session:
            user = User(username="admin", password_hash="unused", role="admin", enabled=True, must_change_password=False)
            session.add(user)
            session.flush()
            admin_id = user.id
        repository = TrainingDatasetRepository(sessions)
        settings = Settings(training_dataset_roots_raw=f"primary={root}")
        service = DatasetService(settings, repository)
        source = service.create({"name": "Completion QA", "root_alias": "primary", "directory": "source", "train_split": "images/train", "val_split": "images/val", "test_split": "images/test", "classes": ["helmet", "no_helmet"]}, created_by=admin_id)
        editing = service.clone_version(source["id"], created_by=admin_id)
        filesystem = DatasetFilesystem(dict(settings.training_dataset_roots))
        app, _, _ = _app(tmp_path)
        app.state.training_dataset_service = service
        app.state.annotation_service = AnnotationService(repository, filesystem)
        app.state.dataset_validator = DatasetValidator(repository, filesystem)
        base = f"/api/training/datasets/{editing['id']}"
        headers = {"Origin": "http://testserver"}
        with TestClient(app) as client:
            def pending(split):
                response = client.get(f"{base}/images?split={split}&status=pending_annotation&page=1&page_size=1")
                assert response.status_code == 200
                return response.json()["total"]

            assert [pending(split) for split in ("train", "val", "test")] == [1, 1, 51]
            second_page = client.get(f"{base}/images?split=test&page=2&page_size=50").json()
            assert second_page["total"] == 51
            assert len(second_page["items"]) == 1
            for split in ("val", "test", "train"):
                for image in repository.list_images(editing["id"], split, page_size=100)["items"]:
                    saved = client.post(f"{base}/images/{image['id']}/confirm-empty", json={"revision": image["revision"]}, headers=headers)
                    assert saved.status_code == 200
                if split == "test":
                    assert [pending(part) for part in ("train", "val", "test")] == [1, 0, 0]
                    blocked = client.post(f"{base}/publish", json={"revision": editing["revision"], "confirm_warnings": True}, headers=headers)
                    assert blocked.status_code == 409
                    assert any(issue["code"] == "annotation_pending" and issue["split"] == "train" for issue in blocked.json()["validation"]["errors"])
            assert [pending(split) for split in ("train", "val", "test")] == [0, 0, 0]
            result = client.post(f"{base}/publish", json={"revision": editing["revision"], "confirm_warnings": True}, headers=headers)
            assert result.status_code == 200
            assert result.json()["status"] == "published"
    finally:
        engine.dispose()
