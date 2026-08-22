from __future__ import annotations

from pathlib import Path

from PIL import Image
import pytest

from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.training.dataset_repository import TrainingDatasetRepository
from backend.training.dataset_service import DatasetService


@pytest.fixture
def annotation_context(tmp_path):
    from backend.training.annotation_service import AnnotationService
    from backend.training.dataset_filesystem import DatasetFilesystem

    root = tmp_path / "datasets"
    for kind in ("images", "labels"):
        for split in ("train", "val", "test"):
            (root / "source" / kind / split).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 80), "white").save(root / "source/images/test/image.jpg")
    url = "sqlite:///" + (tmp_path / "annotations.db").as_posix()
    run_migrations(url)
    engine = create_engine_from_url(url)
    sessions = create_session_factory(engine)
    with sessions.begin() as session:
        user = User(username="admin", password_hash="x", role="admin", enabled=True, must_change_password=False)
        session.add(user); session.flush(); admin_id = user.id
    repository = TrainingDatasetRepository(sessions)
    settings = Settings(training_dataset_roots_raw=f"primary={root}")
    datasets = DatasetService(settings, repository)
    source = datasets.create({
        "name": "PPE", "root_alias": "primary", "directory": "source",
        "train_split": "images/train", "val_split": "images/val",
        "test_split": "images/test", "classes": ["person", "helmet", "no_helmet"],
    }, created_by=admin_id)
    editing = datasets.clone_version(source["id"], created_by=admin_id)
    image = datasets.list_images(editing["id"], split="test")["items"][0]
    service = AnnotationService(repository, DatasetFilesystem(dict(settings.training_dataset_roots)))
    try:
        yield service, repository, root / editing["directory"], editing, image
    finally:
        engine.dispose()


def test_save_annotation_creates_yolo_label_and_increments_revision(annotation_context):
    service, _repository, path, editing, image = annotation_context
    saved = service.save(editing["id"], image["id"], image["revision"], [{
        "class_id": 2, "x_center": .5, "y_center": .25, "width": .2, "height": .1,
    }])
    assert saved["review_status"] == "annotated"
    assert saved["revision"] == 1
    assert (path / "labels/test/image.txt").read_text(encoding="utf-8") == (
        "2 0.500000 0.250000 0.200000 0.100000\n"
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.01, 1.01, True, "0.5"])
def test_save_rejects_nonfinite_out_of_range_or_non_numeric_coordinates(annotation_context, bad):
    from backend.training.annotation_service import AnnotationServiceError
    service, _repository, _path, editing, image = annotation_context
    with pytest.raises(AnnotationServiceError) as error:
        service.save(editing["id"], image["id"], 0, [{
            "class_id": 2, "x_center": bad, "y_center": .25, "width": .2, "height": .1,
        }])
    assert error.value.code == "annotation_invalid"


def test_confirm_empty_is_distinct_from_unsaved_empty_boxes(annotation_context):
    from backend.training.annotation_service import AnnotationServiceError
    service, _repository, path, editing, image = annotation_context
    with pytest.raises(AnnotationServiceError):
        service.save(editing["id"], image["id"], 0, [])
    saved = service.confirm_empty(editing["id"], image["id"], 0)
    assert saved["review_status"] == "confirmed_empty"
    assert (path / "labels/test/image.txt").read_bytes() == b""


def test_revision_conflict_restores_previous_label(annotation_context):
    from backend.training.annotation_service import AnnotationServiceError
    service, _repository, path, editing, image = annotation_context
    service.save(editing["id"], image["id"], 0, [{
        "class_id": 0, "x_center": .5, "y_center": .5, "width": .5, "height": .5,
    }])
    before = (path / "labels/test/image.txt").read_bytes()
    with pytest.raises(AnnotationServiceError) as error:
        service.save(editing["id"], image["id"], 0, [{
            "class_id": 1, "x_center": .2, "y_center": .2, "width": .1, "height": .1,
        }])
    assert error.value.code == "annotation_conflict"
    assert (path / "labels/test/image.txt").read_bytes() == before


def test_classes_append_and_keep_existing_ids(annotation_context):
    service, repository, _path, editing, _image = annotation_context
    first = service.add_class(editing["id"], editing["revision"], "vest")
    second = service.rename_class(editing["id"], first["revision"], 3, "safety_vest")
    assert second["classes"] == ["person", "helmet", "no_helmet", "safety_vest"]
    assert repository.get(editing["id"])["classes"][2] == "no_helmet"


def test_only_unused_trailing_class_can_be_deleted(annotation_context):
    from backend.training.annotation_service import AnnotationServiceError
    service, _repository, _path, editing, image = annotation_context
    added = service.add_class(editing["id"], editing["revision"], "vest")
    with pytest.raises(AnnotationServiceError) as middle:
        service.delete_class(editing["id"], added["revision"], 1)
    assert middle.value.code == "class_order_locked"
    saved = service.save(editing["id"], image["id"], 0, [{
        "class_id": 3, "x_center": .5, "y_center": .5, "width": .2, "height": .2,
    }])
    with pytest.raises(AnnotationServiceError) as used:
        service.delete_class(editing["id"], added["revision"], 3)
    assert used.value.code == "class_in_use"
    assert saved["review_status"] == "annotated"
