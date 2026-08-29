from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import ModelVersion, TrainingJob, User
from backend.db.repositories import Repository
from backend.db.session import create_engine_from_url, create_session_factory
from backend.training.catalog import TrainingRequest
from backend.training.dataset_repository import TrainingDatasetRepository
from backend.training.dataset_service import DatasetService
from backend.training.repository import TrainingRepository
from backend.training.service import TrainingService, TrainingServiceError
from backend.training.storage import TrainingStorage


class ManagerSpy:
    def __init__(self):
        self.wakes = 0

    def wake(self):
        self.wakes += 1

    def owns_job(self, _job_id: int) -> bool:
        return False


@pytest.fixture
def service_context(tmp_path: Path):
    database_url = "sqlite:///" + (tmp_path / "service.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
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
    dataset_root = tmp_path / "datasets"
    dataset_dir = dataset_root / "site-a"
    for kind in ("images", "labels"):
        for split in ("train", "val", "test"):
            (dataset_dir / kind / split).mkdir(parents=True, exist_ok=True)
    dataset = dataset_dir / "data.yaml"
    dataset.write_text(
        "path: .\ntrain: images/train\nval: images/val\ntest: images/test\n"
        "names: [person, helmet, no_helmet]\n",
        encoding="utf-8",
    )
    settings = Settings(
        database_url=database_url,
        training_dataset_yaml=dataset,
        training_dataset_roots_raw=f"primary={dataset_root}",
        training_run_dir=tmp_path / "runs",
        training_model_cache_dir=tmp_path / "models",
    )
    repository = TrainingRepository(session_factory)
    dataset_repository = TrainingDatasetRepository(session_factory)
    dataset_service = DatasetService(settings, dataset_repository)
    dataset_service.create(
        {
            "name": "Site A",
            "root_alias": "primary",
            "directory": "site-a",
            "train_split": "images/train",
            "val_split": "images/val",
            "test_split": "images/test",
            "classes": ["person", "helmet", "no_helmet"],
        },
        created_by=admin_id,
    )
    model_repository = Repository(session_factory)
    storage = TrainingStorage(settings)
    storage.model_cache_root.mkdir(parents=True)
    (storage.model_cache_root / "yolo11m.pt").write_bytes(b"local-weights")
    manager = ManagerSpy()
    service = TrainingService(
        repository, storage, model_repository, manager, settings, dataset_service
    )
    try:
        yield (
            service,
            repository,
            storage,
            manager,
            session_factory,
            admin_id,
            settings,
        )
    finally:
        engine.dispose()


def request(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "dataset_key": "dataset:1",
        "executor": "local",
        "base_model": "yolo11m.pt",
        "imgsz": 640,
        "epochs": 3,
        "batch": "4",
        "device": "cpu",
        "patience": 2,
    }
    payload.update(overrides)
    return payload


def test_create_writes_complete_manifest_before_queue_commit_and_is_idempotent(
    service_context,
):
    """Catches a claimable job without a manifest or duplicate retry directory."""
    service, repository, storage, manager, _factory, admin_id, _settings = service_context
    original_create = repository.create_job
    observed_manifest: dict[str, object] = {}

    def asserting_create(values):
        nonlocal observed_manifest
        manifest = storage.run_path(values["run_key"]) / "manifest.json"
        if not observed_manifest:
            observed_manifest = json.loads(manifest.read_text(encoding="utf-8"))
        return original_create(values)

    repository.create_job = asserting_create
    first = service.create(
        created_by=admin_id,
        request=request(),
        idempotency_key="create-model-001",
    )
    second = service.create(
        created_by=admin_id,
        request=TrainingRequest(**request()),
        idempotency_key="create-model-001",
    )

    assert second["id"] == first["id"]
    assert observed_manifest["base_model"] == "yolo11m.pt"
    assert observed_manifest["dataset_yaml"] == str(
        storage.run_path(first["run_key"]) / "dataset.yaml"
    )
    assert Path(str(observed_manifest["run_dir"])).is_relative_to(storage.root)
    assert observed_manifest.get("resume_checkpoint") is None
    assert first["dataset_classes"] == ["person", "helmet", "no_helmet"]
    assert repository.list_jobs()["total"] == 1
    assert len(list(storage.root.iterdir())) == 1
    assert manager.wakes == 2


def test_create_rejects_reusing_idempotency_key_for_different_configuration(
    service_context,
):
    """Catches one retry key silently returning an unrelated training job."""
    service, repository, storage, _manager, _factory, admin_id, _settings = service_context
    service.create(
        created_by=admin_id,
        request=request(imgsz=640),
        idempotency_key="configuration-key-1",
    )

    with pytest.raises(TrainingServiceError) as captured:
        service.create(
            created_by=admin_id,
            request=request(imgsz=960),
            idempotency_key="configuration-key-1",
        )

    assert captured.value.code == "idempotency_conflict"
    assert repository.list_jobs()["total"] == 1
    assert len(list(storage.root.iterdir())) == 1


def test_create_persists_complete_advanced_and_acceptance_snapshots(service_context):
    service, _repository, storage, _manager, _factory, admin_id, _settings = service_context

    created = service.create(
        created_by=admin_id,
        request=request(
            mode="advanced",
            profile="high_recall",
            advanced_config={"lr0": 0.002},
            acceptance_config={
                "overall": {"map50": 0.6},
                "classes": {"no_helmet": {"recall": 0.8}},
            },
        ),
        idempotency_key="advanced-create-1",
    )

    assert created["config_schema_version"] == 1
    assert created["training_profile"] == "high_recall"
    assert created["advanced_config"]["lr0"] == 0.002
    assert created["acceptance_config"]["overall"]["map50"] == 0.6
    manifest = json.loads(
        (storage.run_path(created["run_key"]) / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["advanced_config"] == created["advanced_config"]
    assert manifest["acceptance_config"] == created["acceptance_config"]


def test_clone_template_returns_only_normalized_create_fields(service_context):
    service, _repository, _storage, _manager, _factory, admin_id, _settings = service_context
    source = service.create(
        created_by=admin_id,
        request=request(mode="advanced", profile="small_object"),
        idempotency_key="clone-source-1",
    )

    cloned = service.clone_template(source["id"])

    assert cloned["profile"] == "small_object"
    assert cloned["advanced_config"] == source["advanced_config"]
    assert cloned["acceptance_config"] == source["acceptance_config"]
    assert set(cloned) == {
        "dataset_key", "executor", "base_model", "imgsz", "epochs", "batch",
        "device", "patience", "mode", "profile", "advanced_config",
        "acceptance_config",
    }


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (request(executor="remote"), "executor_unavailable"),
        (request(base_model="../../private.pt"), "invalid_training_request"),
    ],
)
def test_create_maps_untrusted_requests_to_stable_errors(service_context, payload, code):
    """Catches raw catalog validation details or paths escaping the service boundary."""
    service, _repository, _storage, _manager, _factory, admin_id, _settings = service_context

    with pytest.raises(TrainingServiceError) as captured:
        service.create(
            created_by=admin_id,
            request=payload,
            idempotency_key="invalid-request-01",
        )

    assert captured.value.code == code
    assert "private" not in captured.value.message.casefold()


def test_create_rejects_unavailable_dataset_before_allocating_run(service_context):
    """Catches a known-invalid configured dataset being queued for the worker."""
    service, _repository, storage, _manager, _factory, admin_id, settings = service_context
    (settings.training_dataset_yaml.parent / "images" / "test").rmdir()

    with pytest.raises(TrainingServiceError) as captured:
        service.create(
            created_by=admin_id,
            request=request(),
            idempotency_key="missing-dataset-1",
        )

    assert captured.value.code == "dataset_unavailable"
    assert not storage.root.exists()


def test_create_rejects_missing_local_weights_before_allocating_run(service_context):
    service, _repository, storage, _manager, _factory, admin_id, _settings = service_context

    with pytest.raises(TrainingServiceError) as captured:
        service.create(
            created_by=admin_id,
            request=request(base_model="yolo11s.pt"),
            idempotency_key="missing-weights-1",
        )

    assert captured.value.code == "weights_unavailable"
    assert not storage.root.exists()


def test_create_removes_new_run_when_database_persistence_fails(service_context):
    """Catches orphaned manifests and directories after a queue insert failure."""
    service, repository, storage, _manager, _factory, admin_id, _settings = service_context

    def fail_create(_values):
        raise RuntimeError("database unavailable")

    repository.create_job = fail_create
    with pytest.raises(TrainingServiceError) as captured:
        service.create(
            created_by=admin_id,
            request=request(),
            idempotency_key="database-failure-1",
        )

    assert captured.value.code == "training_create_failed"
    assert list(storage.root.iterdir()) == []


def test_missing_job_operations_return_one_stable_error(service_context):
    """Catches repository None values surfacing as unsafe attribute errors."""
    service, _repository, _storage, _manager, _factory, _admin_id, _settings = service_context

    for operation in (
        lambda: service.get_job(999),
        lambda: service.request_stop(999),
        lambda: service.resume(999, idempotency_key="resume-missing-1"),
        lambda: service.register_model(999),
    ):
        with pytest.raises(TrainingServiceError) as captured:
            operation()
        assert captured.value.code == "training_job_not_found"


def make_resumable(repository, storage, admin_id: int, suffix: str = "1"):
    run_key = storage.create_run()
    storage.write_dataset_snapshot(
        run_key,
        {
            "path": storage.dataset_yaml.parent,
            "train": "images/train",
            "val": "images/val",
            "test": "images/test",
            "classes": ["person", "helmet", "no_helmet"],
        },
    )
    created = repository.create_job(
        {
            "created_by": admin_id,
            "executor": "local",
            "base_model": "yolo26l.pt",
            "dataset_key": "dataset:1",
            "training_dataset_id": 1,
            "epochs": 20,
            "imgsz": 960,
            "batch": "2",
            "device": "0",
            "patience": 5,
            "run_key": run_key,
            "dataset_classes": ["person", "helmet", "no_helmet"],
            "idempotency_key": f"resume-source-{suffix}",
        }
    )
    running = repository.claim_next()
    source_last = storage.resolve_artifact(created["run_key"], "last")
    source_last.parent.mkdir(parents=True)
    source_last.write_bytes(b"checkpoint-to-resume")
    repository.mark_terminal(
        running["id"], status="interrupted", has_last=True
    )
    return repository.get_job(created["id"]), source_last


def test_resume_copies_only_contained_last_checkpoint_into_new_manifest(service_context):
    """Catches resume starting fresh or handing the worker an old/uncontained path."""
    service, repository, storage, manager, _factory, admin_id, _settings = service_context
    source, source_last = make_resumable(repository, storage, admin_id)

    resumed = service.resume(source["id"], idempotency_key="resume-attempt-1")

    manifest_path = storage.run_path(resumed["run_key"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checkpoint = Path(manifest["resume_checkpoint"])
    assert checkpoint.is_relative_to(storage.run_path(resumed["run_key"]))
    assert checkpoint.read_bytes() == source_last.read_bytes()
    assert resumed["resume_from_id"] == source["id"]
    assert manager.wakes == 1


def test_resume_rejects_last_checkpoint_symlink_that_escapes_storage(service_context):
    """Catches a persisted has_last flag authorizing a symlinked external model."""
    service, repository, storage, _manager, _factory, admin_id, _settings = service_context
    source, source_last = make_resumable(repository, storage, admin_id)
    source_last.unlink()
    outside = storage.root.parent / "outside.pt"
    outside.write_bytes(b"outside")
    try:
        source_last.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    with pytest.raises(TrainingServiceError) as captured:
        service.resume(source["id"], idempotency_key="resume-escaped-1")

    assert captured.value.code == "resume_checkpoint_unavailable"


def test_resume_idempotency_rejects_other_source_and_create_key_collisions(
    service_context,
):
    """Catches one idempotency key returning an unrelated create/resume attempt."""
    service, repository, storage, _manager, _factory, admin_id, _settings = service_context
    first, _ = make_resumable(repository, storage, admin_id, "collision-a")
    resumed = service.resume(first["id"], idempotency_key="shared-resume-key")
    repository.request_stop(resumed["id"])
    second, _ = make_resumable(repository, storage, admin_id, "collision-b")

    with pytest.raises(TrainingServiceError) as other_source:
        service.resume(second["id"], idempotency_key="shared-resume-key")
    assert other_source.value.code == "idempotency_conflict"
    assert repository.get_job(resumed["id"])["resume_from_id"] == first["id"]

    created = service.create(
        created_by=admin_id,
        request=request(),
        idempotency_key="ordinary-create-key",
    )
    with pytest.raises(TrainingServiceError) as create_collision:
        service.resume(second["id"], idempotency_key="ordinary-create-key")
    assert create_collision.value.code == "idempotency_conflict"
    assert repository.get_job(created["id"])["resume_from_id"] is None


def test_failed_evaluation_at_total_epochs_is_not_offered_as_resumable(
    service_context,
):
    """Catches a guaranteed-failing resume button for a fully trained checkpoint."""
    service, repository, storage, _manager, _factory, admin_id, _settings = service_context
    source, _last = make_resumable(repository, storage, admin_id, "evaluation")
    with repository._session_factory.begin() as session:
        row = session.get(TrainingJob, source["id"])
        row.current_epoch = row.total_epochs
        row.status = "failed"
        row.error_code = "evaluation_failed"

    with pytest.raises(TrainingServiceError) as captured:
        service.resume(source["id"], idempotency_key="evaluation-resume")

    assert captured.value.code == "evaluation_retry_required"


def completed_job(repository, storage, admin_id: int):
    created = repository.create_job(
        {
            "created_by": admin_id,
            "executor": "local",
            "base_model": "yolo11l.pt",
            "dataset_key": "configured",
            "dataset_classes": ["person", "helmet", "no_helmet"],
            "epochs": 10,
            "imgsz": 640,
            "batch": "4",
            "device": "0",
            "patience": 3,
            "run_key": storage.create_run(),
            "idempotency_key": "completed-source-1",
        }
    )
    running = repository.claim_next()
    best = storage.resolve_artifact(created["run_key"], "best")
    best.parent.mkdir(parents=True)
    best.write_bytes(b"trained best checkpoint")
    digest = hashlib.sha256(best.read_bytes()).hexdigest()
    metrics = {
        "precision": 0.89,
        "recall": 0.93,
        "map50": 0.90,
        "map50_95": 0.67,
        "classes": {
            "no_helmet": {
                "precision": 0.87,
                "recall": 0.92,
                "map50": 0.9,
                "map50_95": 0.65,
            }
        },
        "no_helmet_precision": 0.87,
        "no_helmet_recall": 0.92,
        "accepted": True,
    }
    repository.mark_terminal(
        running["id"],
        status="completed",
        metrics=metrics,
        has_best=True,
        best_sha256=digest,
    )
    return repository.get_job(created["id"]), best


def test_register_model_requires_unchanged_completed_best_and_keeps_candidate_inactive(
    service_context,
):
    """Catches auto-activation, lost provenance, or registering changed weights."""
    service, repository, storage, _manager, factory, admin_id, _settings = service_context
    job, best = completed_job(repository, storage, admin_id)

    candidate = service.register_model(job["id"], name="helmet candidate")

    assert "file_path" not in candidate
    with factory() as session:
        row = session.scalar(
            select(ModelVersion).where(ModelVersion.training_job_id == job["id"])
        )
        assert row is not None
        assert row.file_path == str(best.resolve())
        assert row.is_active is False
        assert row.source == "training"
        assert row.base_model == "yolo11l.pt"
        assert row.imgsz == 640
    assert json.loads(row.metrics_json)["classes"]["no_helmet"]["recall"] == 0.92


def test_delete_removes_terminal_unregistered_training_job(service_context):
    service, repository, storage, _manager, _factory, admin_id, _settings = service_context
    job, _best = completed_job(repository, storage, admin_id)
    run_dir = storage.run_path(job["run_key"])

    service.delete(job["id"])

    assert repository.get_job(job["id"]) is None
    assert not run_dir.exists()


def test_register_model_rejects_best_changed_after_evaluation(service_context):
    """Catches a replaced checkpoint bypassing the held-out evaluation digest."""
    service, repository, storage, _manager, factory, admin_id, _settings = service_context
    job, best = completed_job(repository, storage, admin_id)
    best.write_bytes(b"changed after evaluation")

    with pytest.raises(TrainingServiceError) as captured:
        service.register_model(job["id"])

    assert captured.value.code == "model_artifact_changed"
    with factory() as session:
        assert session.scalar(select(ModelVersion.id)) is None


def test_register_model_is_idempotent_and_uses_training_time_class_snapshot(
    service_context,
):
    """Catches duplicate candidates or YAML edits changing trained model classes."""
    service, repository, storage, _manager, factory, admin_id, settings = service_context
    job, _best = completed_job(repository, storage, admin_id)
    settings.training_dataset_yaml.write_text(
        "train: changed\nval: changed\ntest: changed\nnames: [wrong]\n",
        encoding="utf-8",
    )

    first = service.register_model(job["id"], name="original candidate")
    second = service.register_model(job["id"], name="retry candidate")

    assert second["id"] == first["id"]
    with factory() as session:
        rows = session.scalars(
            select(ModelVersion).where(ModelVersion.training_job_id == job["id"])
        ).all()
        assert len(rows) == 1
        assert json.loads(rows[0].classes) == ["person", "helmet", "no_helmet"]
