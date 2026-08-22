from __future__ import annotations

from pathlib import Path

from PIL import Image
import pytest

from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.training.dataset_repository import TrainingDatasetRepository
from backend.training.dataset_service import DatasetService, DatasetServiceError


def _tree(root: Path) -> Path:
    dataset = root / "source"
    for kind in ("images", "labels"):
        for split in ("train", "val", "test"):
            (dataset / kind / split).mkdir(parents=True)
    Image.new("RGB", (80, 60), "white").save(dataset / "images/test/existing.jpg")
    (dataset / "labels/test/existing.txt").write_text(
        "0 0.500000 0.500000 0.250000 0.250000\n", encoding="utf-8"
    )
    (dataset / "labels/test.cache").write_bytes(b"cache")
    return dataset


@pytest.fixture
def version_context(tmp_path):
    root = tmp_path / "authorized"
    root.mkdir()
    source_path = _tree(root)
    settings = Settings(training_dataset_roots_raw=f"primary={root}")
    url = "sqlite:///" + (tmp_path / "versions.db").as_posix()
    run_migrations(url)
    engine = create_engine_from_url(url)
    sessions = create_session_factory(engine)
    with sessions.begin() as session:
        user = User(
            username="admin", password_hash="unused", role="admin",
            enabled=True, must_change_password=False,
        )
        session.add(user)
        session.flush()
        admin_id = user.id
    repository = TrainingDatasetRepository(sessions)
    service = DatasetService(settings, repository)
    source = service.create({
        "name": "Source", "root_alias": "primary", "directory": "source",
        "train_split": "images/train", "val_split": "images/val",
        "test_split": "images/test", "classes": ["person", "helmet", "no_helmet"],
    }, created_by=admin_id)
    try:
        yield service, repository, root, source_path, source, admin_id
    finally:
        engine.dispose()


def test_clone_creates_isolated_editing_copy_and_skips_cache(version_context):
    service, _repository, root, source_path, source, admin_id = version_context

    editing = service.clone_version(source["id"], created_by=admin_id)

    destination = root / editing["directory"]
    assert editing["status"] == "editing"
    assert destination != source_path
    assert (destination / "images/test/existing.jpg").is_file()
    assert not (destination / "labels/test.cache").exists()
    assert service.list_images(
        editing["id"], split="test", status="pending_annotation"
    )["total"] == 1


def test_rescan_discovers_server_added_unlabeled_image(version_context):
    service, _repository, root, _source_path, source, admin_id = version_context
    editing = service.clone_version(source["id"], created_by=admin_id)
    destination = root / editing["directory"]
    Image.new("RGB", (320, 240), "black").save(destination / "images/test/new.jpg")

    result = service.rescan(editing["id"], "test")

    assert result["discovered"] == 1
    items = service.list_images(
        editing["id"], split="test", status="pending_annotation"
    )["items"]
    assert [(item["display_name"], item["width"], item["height"]) for item in items] == [
        ("existing.jpg", 80, 60), ("new.jpg", 320, 240),
    ]


def test_rescan_marks_external_image_change_without_accepting_it(version_context):
    service, _repository, root, _source_path, source, admin_id = version_context
    editing = service.clone_version(source["id"], created_by=admin_id)
    destination = root / editing["directory"]
    Image.new("RGB", (100, 100), "red").save(destination / "images/test/existing.jpg")

    result = service.rescan(editing["id"], "test")

    assert result["changed"] == 1
    item = service.list_images(editing["id"], split="test")["items"][0]
    assert item["validation_code"] == "file_changed"
    assert (item["width"], item["height"]) == (80, 60)


def test_published_dataset_cannot_be_rescanned(version_context):
    service, _repository, _root, _source_path, source, _admin_id = version_context

    with pytest.raises(DatasetServiceError) as error:
        service.rescan(source["id"], "test")

    assert error.value.code == "dataset_published"
