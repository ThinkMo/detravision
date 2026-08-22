from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.training.annotation_service import AnnotationService
from backend.training.dataset_filesystem import DatasetFilesystem
from backend.training.dataset_repository import TrainingDatasetRepository
from backend.training.dataset_service import DatasetService
from backend.training.dataset_validation import DatasetValidationError, DatasetValidator


@pytest.fixture
def validation_context(tmp_path):
    root = tmp_path / "datasets"
    root.mkdir()
    source = root / "source"
    for kind in ("images", "labels"):
        for split in ("train", "val", "test"):
            (source / kind / split).mkdir(parents=True)
    Image.new("RGB", (80, 60), "white").save(source / "images/test/sample.jpg")
    (source / "labels/test/sample.txt").write_text(
        "0 0.500000 0.500000 0.400000 0.800000\n", encoding="utf-8"
    )
    settings = Settings(training_dataset_roots_raw=f"primary={root}")
    url = "sqlite:///" + (tmp_path / "validation.db").as_posix()
    run_migrations(url)
    engine = create_engine_from_url(url)
    sessions = create_session_factory(engine)
    with sessions.begin() as session:
        user = User(
            username="admin", password_hash="unused", role="admin", enabled=True,
            must_change_password=False,
        )
        session.add(user)
        session.flush()
        user_id = user.id
    repository = TrainingDatasetRepository(sessions)
    service = DatasetService(settings, repository)
    published = service.create(
        {
            "name": "PPE", "root_alias": "primary", "directory": "source",
            "train_split": "images/train", "val_split": "images/val",
            "test_split": "images/test",
            "classes": ["person", "helmet", "no_helmet"],
        },
        created_by=user_id,
    )
    editing = service.clone_version(published["id"], created_by=user_id)
    filesystem = DatasetFilesystem({"primary": root})
    annotations = AnnotationService(repository, filesystem)
    validator = DatasetValidator(repository, filesystem)
    try:
        yield service, repository, annotations, validator, filesystem, editing
    finally:
        engine.dispose()


def _only_image(repository, dataset_id):
    return repository.list_images(dataset_id, "test", page=1, page_size=10)["items"][0]


def test_pending_image_blocks_publication(validation_context):
    _service, _repository, _annotations, validator, _filesystem, editing = validation_context

    report = validator.validate(editing["id"])

    assert any(issue.code == "annotation_pending" for issue in report.errors)
    with pytest.raises(DatasetValidationError) as raised:
        validator.publish(editing["id"], editing["revision"], False)
    assert raised.value.code == "dataset_validation_failed"


def test_confirmed_empty_requires_warning_confirmation_then_publishes(validation_context):
    _service, repository, annotations, validator, _filesystem, editing = validation_context
    image = _only_image(repository, editing["id"])
    annotations.confirm_empty(editing["id"], image["id"], image["revision"])

    first = validator.validate(editing["id"])
    second = validator.validate(editing["id"])

    assert first.errors == []
    assert first.warnings
    assert first.digest == second.digest
    with pytest.raises(DatasetValidationError) as raised:
        validator.publish(editing["id"], editing["revision"], False)
    assert raised.value.code == "dataset_warnings_unconfirmed"
    published = validator.publish(editing["id"], editing["revision"], True)
    assert published["status"] == "published"
    assert len(published["manifest_digest"]) == 64


def test_invalid_label_is_reported_without_exposing_paths(validation_context):
    _service, repository, annotations, validator, filesystem, editing = validation_context
    image = _only_image(repository, editing["id"])
    annotations.save(
        editing["id"], image["id"], image["revision"],
        [{"class_id": 0, "x_center": 0.5, "y_center": 0.5, "width": 0.2, "height": 0.4}],
    )
    dataset = repository.get(editing["id"])
    assert dataset is not None
    label = filesystem.resolve_label(dataset, "test", image["image_key"])
    label.write_text("99 0.5 0.5 0.2 0.2\n", encoding="utf-8")

    report = validator.validate(editing["id"])

    assert any(issue.code == "annotation_invalid" for issue in report.errors)
    assert str(label.parent) not in repr(report)


def test_overlapping_helmet_states_are_reported_as_warning(validation_context):
    _service, repository, annotations, validator, _filesystem, editing = validation_context
    image = _only_image(repository, editing["id"])
    annotations.save(
        editing["id"], image["id"], image["revision"],
        [
            {"class_id": 0, "x_center": 0.5, "y_center": 0.5, "width": 0.5, "height": 0.8},
            {"class_id": 1, "x_center": 0.5, "y_center": 0.2, "width": 0.2, "height": 0.2},
            {"class_id": 2, "x_center": 0.5, "y_center": 0.2, "width": 0.2, "height": 0.2},
        ],
    )

    report = validator.validate(editing["id"])

    assert any(issue.code == "helmet_state_overlap" for issue in report.warnings)


def test_editing_version_is_not_trainable(validation_context):
    service, _repository, _annotations, _validator, _filesystem, editing = validation_context
    from backend.training.dataset_service import DatasetServiceError

    with pytest.raises(DatasetServiceError) as raised:
        service.resolve(editing["key"])

    assert raised.value.code == "dataset_invalid"
