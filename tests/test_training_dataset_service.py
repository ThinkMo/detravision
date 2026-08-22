from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import TrainingJob, User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.training.dataset_repository import TrainingDatasetRepository


@pytest.fixture
def dataset_service_context(tmp_path):
    from backend.training.dataset_service import DatasetService

    root = tmp_path / "authorized"
    root.mkdir()
    settings = Settings(training_dataset_roots_raw=f"primary={root}")
    url = "sqlite:///" + (tmp_path / "dataset-service.db").as_posix()
    run_migrations(url)
    engine = create_engine_from_url(url)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        admin = User(
            username="admin",
            password_hash="unused",
            role="admin",
            enabled=True,
            must_change_password=False,
        )
        session.add(admin)
        session.flush()
        admin_id = admin.id
    repository = TrainingDatasetRepository(session_factory)
    try:
        yield DatasetService(settings, repository), repository, session_factory, root, admin_id
    finally:
        engine.dispose()


def _make_yolo_tree(root: Path, directory: str = "site-a") -> Path:
    dataset = root / directory
    for kind in ("images", "labels"):
        for split in ("train", "val", "test"):
            (dataset / kind / split).mkdir(parents=True, exist_ok=True)
    return dataset


def _valid_definition(admin_id: int) -> dict[str, object]:
    return {
        "name": "Helmet site A",
        "root_alias": "primary",
        "directory": "site-a",
        "train_split": "images/train",
        "val_split": "images/val",
        "test_split": "images/test",
        "classes": ["person", "helmet", "no_helmet"],
    }


def test_browser_returns_only_relative_immediate_children(dataset_service_context):
    service, _repository, _sessions, root, _admin_id = dataset_service_context
    (root / "ppe" / "images").mkdir(parents=True)
    (root / "other").mkdir()
    (root / "not-a-directory.txt").write_text("hidden", encoding="utf-8")

    assert service.list_roots() == [{"alias": "primary"}]
    assert service.list_directories("primary", "") == [
        {"name": "other", "path": "other"},
        {"name": "ppe", "path": "ppe"},
    ]
    assert service.list_directories("primary", "ppe") == [
        {"name": "images", "path": "ppe/images"}
    ]


@pytest.mark.parametrize(
    "relative",
    ["../private", "C:/private", "C:\\private", "//server/share", "/srv/private"],
)
def test_browser_rejects_escaping_paths(dataset_service_context, relative):
    from backend.training.dataset_service import DatasetServiceError

    service, _repository, _sessions, _root, _admin_id = dataset_service_context

    with pytest.raises(DatasetServiceError) as raised:
        service.list_directories("primary", relative)

    assert raised.value.code == "path_invalid"
    assert str(raised.value) == "path_invalid"


def test_browser_rejects_unknown_root_without_leaking_paths(dataset_service_context):
    from backend.training.dataset_service import DatasetServiceError

    service, _repository, _sessions, root, _admin_id = dataset_service_context
    with pytest.raises(DatasetServiceError) as raised:
        service.list_directories("missing", "")

    assert raised.value.code == "root_not_found"
    assert str(root) not in str(raised.value)


def test_browser_skips_a_directory_symlink_that_escapes(dataset_service_context, tmp_path):
    service, _repository, _sessions, root, _admin_id = dataset_service_context
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    assert service.list_directories("primary", "") == []


def test_create_validates_and_returns_only_safe_relative_definition(
    dataset_service_context,
):
    service, _repository, _sessions, root, admin_id = dataset_service_context
    _make_yolo_tree(root)

    created = service.create(_valid_definition(admin_id), created_by=admin_id)

    assert created["key"] == f"dataset:{created['id']}"
    assert created["root_alias"] == "primary"
    assert created["directory"] == "site-a"
    assert created["classes"] == ["person", "helmet", "no_helmet"]
    assert created["available"] is True
    assert created["reason"] is None
    assert str(root) not in repr(created)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"test_split": "images/missing"}, "dataset_invalid"),
        ({"train_split": "../outside"}, "path_invalid"),
        ({"classes": []}, "dataset_invalid"),
        ({"classes": ["person", "person"]}, "dataset_invalid"),
        ({"name": ""}, "dataset_invalid"),
    ],
)
def test_create_rejects_invalid_splits_classes_and_name(
    dataset_service_context, change, code
):
    from backend.training.dataset_service import DatasetServiceError

    service, _repository, _sessions, root, admin_id = dataset_service_context
    _make_yolo_tree(root)
    payload = {**_valid_definition(admin_id), **change}

    with pytest.raises(DatasetServiceError) as raised:
        service.create(payload, created_by=admin_id)

    assert raised.value.code == code


def test_update_revalidates_and_disable_is_reference_safe(dataset_service_context):
    service, repository, sessions, root, admin_id = dataset_service_context
    _make_yolo_tree(root)
    created = service.create(_valid_definition(admin_id), created_by=admin_id)

    updated = service.update(created["id"], {"name": "Renamed"})
    with sessions.begin() as session:
        session.add(
            TrainingJob(
                created_by=admin_id,
                executor="local",
                base_model="yolo11n.pt",
                dataset_key=created["key"],
                dataset_classes='["person","helmet","no_helmet"]',
                training_dataset_id=created["id"],
                epochs=1,
                imgsz=640,
                batch="1",
                device="cpu",
                patience=0,
                total_epochs=1,
                run_key="run-service-reference",
                idempotency_key="service-reference",
            )
        )
    disabled = service.disable(created["id"])

    assert updated["name"] == "Renamed"
    assert disabled["enabled"] is False
    assert disabled["available"] is False
    assert disabled["reason"] == "dataset_disabled"
    assert repository.is_referenced(created["id"]) is True


def test_update_rejects_non_boolean_enabled_value(dataset_service_context):
    from backend.training.dataset_service import DatasetServiceError

    service, _repository, _sessions, root, admin_id = dataset_service_context
    _make_yolo_tree(root)
    created = service.create(_valid_definition(admin_id), created_by=admin_id)

    with pytest.raises(DatasetServiceError) as raised:
        service.update(created["id"], {"enabled": "false"})

    assert raised.value.code == "dataset_invalid"


def test_catalog_marks_a_definition_unavailable_after_split_is_removed(
    dataset_service_context,
):
    service, _repository, _sessions, root, admin_id = dataset_service_context
    _make_yolo_tree(root)
    created = service.create(_valid_definition(admin_id), created_by=admin_id)
    (root / "site-a" / "images" / "test").rmdir()

    item = next(row for row in service.catalog() if row["id"] == created["id"])

    assert item["available"] is False
    assert item["reason"] == "dataset_invalid"
