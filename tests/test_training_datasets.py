from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.config import Settings, _default_training_dataset_roots
from backend.db.migrate import run_migrations
from backend.db.models import TrainingJob, User
from backend.db.session import create_engine_from_url, create_session_factory


def test_training_roots_parse_aliases(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()

    settings = Settings(
        training_dataset_roots_raw=f"primary={first};customer={second}"
    )

    assert [alias for alias, _ in settings.training_dataset_roots] == [
        "primary",
        "customer",
    ]
    assert settings.training_dataset_roots[0][1] == first.resolve()
    assert settings.training_dataset_roots[1][1] == second.resolve()


@pytest.mark.parametrize(
    "value",
    [
        "missing",
        "bad alias=C:\\data",
        "a=relative",
        "a=C:\\missing",
        "a=C:\\data;a=C:\\other",
    ],
)
def test_training_roots_reject_malformed_or_missing_roots(value):
    with pytest.raises(ValueError):
        Settings(training_dataset_roots_raw=value)


def test_training_roots_reject_nested_roots(tmp_path):
    parent = tmp_path / "datasets"
    child = parent / "customer"
    child.mkdir(parents=True)

    with pytest.raises(ValueError, match="nested"):
        Settings(training_dataset_roots_raw=f"all={parent};customer={child}")


def test_default_training_root_is_independent_of_process_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    raw_value = _default_training_dataset_roots()

    assert raw_value == f"default={Path(__file__).resolve().parents[1] / 'datasets'}"


@pytest.fixture
def dataset_repository(tmp_path):
    from backend.training.dataset_repository import TrainingDatasetRepository

    url = "sqlite:///" + (tmp_path / "training-datasets.db").as_posix()
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
    try:
        yield TrainingDatasetRepository(session_factory), session_factory, admin_id
    finally:
        engine.dispose()


def _dataset_values(admin_id: int) -> dict[str, object]:
    return {
        "name": "PPE v2",
        "root_alias": "primary",
        "directory": "ppe-v2",
        "train_split": "images/train",
        "val_split": "images/val",
        "test_split": "images/test",
        "classes": ["person", "helmet", "no_helmet"],
        "created_by": admin_id,
    }


def test_training_dataset_repository_round_trip(dataset_repository):
    repository, _session_factory, admin_id = dataset_repository

    created = repository.create(_dataset_values(admin_id))

    assert created == {
        "id": created["id"],
        "name": "PPE v2",
        "root_alias": "primary",
        "directory": "ppe-v2",
        "train_split": "images/train",
        "val_split": "images/val",
        "test_split": "images/test",
        "classes": ["person", "helmet", "no_helmet"],
        "enabled": True,
        "version": 1,
        "status": "published",
        "parent_id": None,
        "root_dataset_id": None,
        "revision": 0,
        "published_at": created["published_at"],
        "manifest_digest": None,
        "created_by": admin_id,
        "created_at": created["created_at"],
        "updated_at": created["updated_at"],
    }
    assert repository.get(created["id"])["classes"] == [
        "person",
        "helmet",
        "no_helmet",
    ]
    assert [item["id"] for item in repository.list()] == [created["id"]]


def test_training_dataset_repository_creates_editing_version(dataset_repository):
    repository, _session_factory, admin_id = dataset_repository
    source = repository.create(_dataset_values(admin_id))

    editing = repository.create_editing_version(
        source["id"],
        {"directory": "dataset-version-2", "created_by": admin_id},
    )

    assert editing["version"] == 2
    assert editing["status"] == "editing"
    assert editing["parent_id"] == source["id"]
    assert editing["root_dataset_id"] == source["id"]
    assert editing["enabled"] is False
    assert [item["version"] for item in repository.list_versions(source["id"])] == [1, 2]


def test_training_dataset_image_revision_is_compare_and_swap(dataset_repository):
    from backend.training.dataset_repository import TrainingDatasetConflictError

    repository, _session_factory, admin_id = dataset_repository
    source = repository.create(_dataset_values(admin_id))
    editing = repository.create_editing_version(
        source["id"], {"directory": "dataset-version-2", "created_by": admin_id}
    )
    image = repository.upsert_image_index(editing["id"], [{
        "split": "test",
        "image_key": "image1.jpg",
        "display_name": "image1.jpg",
        "width": 640,
        "height": 480,
        "review_status": "pending_annotation",
        "content_digest": "a" * 64,
    }])[0]

    changed = repository.update_image_review(
        image["id"], image["revision"], {"review_status": "annotated"}
    )

    assert changed["revision"] == 1
    assert changed["review_status"] == "annotated"
    with pytest.raises(TrainingDatasetConflictError):
        repository.update_image_review(
            image["id"], image["revision"], {"review_status": "confirmed_empty"}
        )


def test_training_dataset_repository_updates_and_disables_idempotently(
    dataset_repository,
):
    repository, _session_factory, admin_id = dataset_repository
    created = repository.create(_dataset_values(admin_id))

    updated = repository.update(
        created["id"],
        {"name": "PPE v3", "classes": ["person", "helmet", "no_helmet", "vest"]},
    )
    first = repository.disable(created["id"])
    second = repository.disable(created["id"])

    assert updated["name"] == "PPE v3"
    assert updated["classes"][-1] == "vest"
    assert first["enabled"] is False
    assert second["enabled"] is False


def test_training_dataset_repository_reports_job_references(dataset_repository):
    repository, session_factory, admin_id = dataset_repository
    dataset = repository.create(_dataset_values(admin_id))
    assert repository.is_referenced(dataset["id"]) is False
    with session_factory.begin() as session:
        session.add(
            TrainingJob(
                created_by=admin_id,
                executor="local",
                base_model="yolo11n.pt",
                dataset_key=f"dataset:{dataset['id']}",
                dataset_classes='["person","helmet","no_helmet"]',
                training_dataset_id=dataset["id"],
                epochs=1,
                imgsz=640,
                batch="1",
                device="cpu",
                patience=0,
                total_epochs=1,
                run_key="run-dataset-reference",
                idempotency_key="dataset-reference",
            )
        )

    assert repository.is_referenced(dataset["id"]) is True


def test_training_dataset_repository_rejects_duplicate_location(dataset_repository):
    from backend.training.dataset_repository import TrainingDatasetConflictError

    repository, _session_factory, admin_id = dataset_repository
    repository.create(_dataset_values(admin_id))

    with pytest.raises(TrainingDatasetConflictError):
        repository.create({**_dataset_values(admin_id), "name": "Duplicate"})
