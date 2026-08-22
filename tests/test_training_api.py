from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.training_routes import router
from backend.auth import AuthenticatedUser
from backend.auth.dependencies import current_user
from backend.core.config import Settings
from backend.training.service import TrainingServiceError
from backend.training.storage import TrainingStorage


NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)


class FakeAudit:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    def record(self, *args, **kwargs) -> None:
        self.rows.append((args, kwargs))


class FakeTrainingService:
    def __init__(self, job: dict) -> None:
        self.job = job
        self.created: list[dict] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        if kwargs["request"].executor == "remote":
            raise TrainingServiceError(422, "executor_unavailable", "Executor unavailable.")
        return self.job

    def list_jobs(self, **kwargs):
        return {"items": [self.job], "total": 1, **kwargs}

    def get_job(self, job_id: int):
        if job_id != self.job["id"]:
            raise TrainingServiceError(404, "training_job_not_found", "Not found.")
        return self.job

    def request_stop(self, job_id: int):
        return {**self.get_job(job_id), "status": "stopping"}

    def resume(self, job_id: int, *, idempotency_key: str):
        return {**self.get_job(job_id), "id": 2, "status": "queued", "resume_from_id": job_id}

    def register_model(self, job_id: int, *, name: str | None = None):
        self.get_job(job_id)
        return {"id": 9, "name": name or "candidate", "is_active": False, "source": "training"}


class FakeDatasetService:
    def __init__(self) -> None:
        self.item = {
            "id": 3, "key": "dataset:3", "name": "Site A",
            "root_alias": "primary", "directory": "site-a",
            "train_split": "images/train", "val_split": "images/val",
            "test_split": "images/test", "classes": ["person", "helmet", "no_helmet"],
            "enabled": True, "available": True, "reason": None,
        }

    def list_roots(self):
        return [{"alias": "primary"}]

    def list_directories(self, root_alias: str, relative: str):
        if root_alias != "primary":
            raise AssertionError("unexpected root")
        return [{"name": "site-a", "path": "site-a"}] if not relative else []

    def catalog(self):
        return [self.item]

    def create(self, values, *, created_by: int):
        return self.item

    def update(self, dataset_id: int, values):
        return {**self.item, **values}

    def disable(self, dataset_id: int):
        return {**self.item, "enabled": False, "available": False, "reason": "dataset_disabled"}


def _job(run_key: str) -> dict:
    return {
        "id": 1,
        "created_by": 7,
        "executor": "local",
        "base_model": "yolo11m.pt",
        "dataset_key": "dataset:3",
        "dataset_classes": ["helmet", "no_helmet"],
        "epochs": 100,
        "imgsz": 640,
        "batch": "auto",
        "device": "auto",
        "patience": 50,
        "status": "completed",
        "current_epoch": 100,
        "total_epochs": 100,
        "pid": 4321,
        "started_at": NOW,
        "finished_at": NOW,
        "stop_requested": False,
        "run_key": run_key,
        "has_best": True,
        "has_last": True,
        "has_metrics": True,
        "precision": .9,
        "recall": .91,
        "map50": .92,
        "map50_95": .7,
        "no_helmet_precision": .88,
        "no_helmet_recall": .91,
        "accepted": True,
        "metrics": {"map50": .92},
        "error_code": None,
        "error_message": None,
        "idempotency_key": "secret-key",
        "best_sha256": "a" * 64,
        "resume_from_id": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _app(tmp_path: Path, *, role: str = "admin") -> tuple[FastAPI, FakeTrainingService, FakeAudit]:
    dataset = tmp_path / "dataset.yaml"
    dataset.write_text("train: train\nval: val\ntest: test\nnames: [helmet, no_helmet]\n", encoding="utf-8")
    settings = Settings(
        training_dataset_yaml=dataset,
        training_dataset_roots_raw=f"primary={tmp_path}",
        training_run_dir=tmp_path / "runs",
        training_model_cache_dir=tmp_path / "models",
    )
    storage = TrainingStorage(settings)
    run_key = storage.create_run()
    storage.resolve_artifact(run_key, "log").write_text(
        f"epoch 1 {tmp_path / 'private'}", encoding="utf-8"
    )
    storage.resolve_artifact(run_key, "confusion_matrix").write_bytes(b"png")
    service = FakeTrainingService(_job(run_key))
    audit = FakeAudit()
    app = FastAPI()
    app.include_router(router)
    app.state.settings = settings
    app.state.training_service = service
    app.state.training_storage = storage
    app.state.training_dataset_service = FakeDatasetService()
    app.state.audit_service = audit
    app.dependency_overrides[current_user] = lambda: AuthenticatedUser(
        id=7, username="tester", role=role, must_change_password=False
    )
    return app, service, audit


def test_training_catalog_and_job_dtos_hide_server_fields(tmp_path: Path) -> None:
    app, _, _ = _app(tmp_path)
    with TestClient(app) as client:
        catalog = client.get("/api/training/catalog")
        listed = client.get("/api/training-jobs")
        detail = client.get("/api/training-jobs/1")

    assert catalog.status_code == 200
    assert len(catalog.json()["models"]) == 10
    assert catalog.json()["executors"][-1] == {"id": "remote", "enabled": False}
    assert listed.status_code == detail.status_code == 200
    serialized = listed.text + detail.text
    for forbidden in ("run_key", "pid", "idempotency_key", "best_sha256", str(tmp_path)):
        assert forbidden not in serialized
    assert detail.json()["confusion_matrix_url"].endswith("/artifacts/confusion_matrix")
    assert detail.json()["log_url"].endswith("/artifacts/log")


def test_training_mutations_use_idempotency_and_stable_errors(tmp_path: Path) -> None:
    app, service, audit = _app(tmp_path)
    payload = {
        "dataset_key": "dataset:3", "executor": "local", "base_model": "yolo26x.pt", "imgsz": 960,
        "epochs": 10, "batch": "auto", "device": "0", "patience": 5,
    }
    with TestClient(app) as client:
        created = client.post("/api/training-jobs", json=payload, headers={"Idempotency-Key": "request_123"})
        stopped = client.post("/api/training-jobs/1/stop")
        resumed = client.post("/api/training-jobs/1/resume", headers={"Idempotency-Key": "resume_123"})
        registered = client.post("/api/training-jobs/1/register-model", json={"name": "candidate"})
        remote = client.post(
            "/api/training-jobs", json={**payload, "executor": "remote"},
            headers={"Idempotency-Key": "request_456"},
        )

    assert created.status_code == 201
    assert service.created[0]["idempotency_key"] == "request_123"
    assert stopped.json()["status"] == "stopping"
    assert resumed.json()["resume_from_id"] == 1
    assert registered.json()["is_active"] is False
    assert remote.status_code == 422
    assert remote.json() == {"code": "executor_unavailable", "message": "Executor unavailable."}
    assert any(row[0][1:4] == ("training_create", "training_job", 1) for row in audit.rows)


def test_training_validation_uses_stable_error_shape(tmp_path: Path) -> None:
    app, _, _ = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/training-jobs",
            json={
                "dataset_key": "dataset:3", "executor": "local", "base_model": "yolo11m.pt", "imgsz": 640,
                "epochs": 0, "batch": "auto", "device": "auto", "patience": 50,
            },
            headers={"Idempotency-Key": "request_789"},
        )
    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_training_request",
        "message": "The training request is invalid.",
    }


def test_training_artifacts_are_allowlisted_and_logs_are_scrubbed(tmp_path: Path) -> None:
    app, _, _ = _app(tmp_path)
    with TestClient(app) as client:
        log = client.get("/api/training-jobs/1/artifacts/log")
        image = client.get("/api/training-jobs/1/artifacts/confusion_matrix")
        rejected = client.get("/api/training-jobs/1/artifacts/best")

    assert log.status_code == 200
    assert "private" not in log.text
    assert "redacted" in log.text
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert rejected.status_code == 404


def test_training_api_requires_admin_and_same_origin(tmp_path: Path) -> None:
    app, _, _ = _app(tmp_path, role="user")
    with TestClient(app) as client:
        assert client.get("/api/training/catalog").status_code == 403
        assert client.post(
            "/api/training-jobs/1/stop",
            headers={"Origin": "https://evil.invalid"},
        ).status_code == 403


def test_training_dataset_admin_routes_are_safe_and_audited(tmp_path: Path) -> None:
    app, _service, audit = _app(tmp_path)
    definition = {
        "name": "Site A", "root_alias": "primary", "directory": "site-a",
        "train_split": "images/train", "val_split": "images/val",
        "test_split": "images/test", "classes": ["person", "helmet", "no_helmet"],
    }
    with TestClient(app) as client:
        roots = client.get("/api/training/dataset-roots")
        directories = client.get(
            "/api/training/dataset-directories", params={"root_alias": "primary", "path": ""}
        )
        listed = client.get("/api/training/datasets")
        created = client.post("/api/training/datasets", json=definition)
        updated = client.patch("/api/training/datasets/3", json={"name": "Renamed"})
        disabled = client.delete("/api/training/datasets/3")

    assert roots.status_code == directories.status_code == listed.status_code == 200
    assert created.status_code == 201
    assert updated.status_code == 200
    assert disabled.status_code == 204
    serialized = roots.text + directories.text + listed.text + created.text + updated.text
    assert str(tmp_path) not in serialized
    assert any(row[0][1] == "training_dataset_create" for row in audit.rows)


def test_training_job_create_requires_dataset_key(tmp_path: Path) -> None:
    app, _service, _audit = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/training-jobs",
            json={
                "executor": "local", "base_model": "yolo11m.pt", "imgsz": 640,
                "epochs": 1, "batch": "1", "device": "cpu", "patience": 0,
            },
            headers={"Idempotency-Key": "missing-dataset-key"},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_training_request"
