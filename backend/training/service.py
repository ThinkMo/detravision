from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from backend.core.config import Settings
from backend.core.public_records import public_model
from backend.db.repositories import Repository
from training.contracts import TrainingManifest, atomic_write_json

from .catalog import TrainingRequest, training_catalog
from .repository import TrainingRepository
from .storage import TrainingStorage


_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")


class TrainingManager(Protocol):
    def wake(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TrainingServiceError(Exception):
    status_code: int
    code: str
    message: str


class TrainingService:
    """Orchestrate trusted manifests, persisted jobs, and candidate models."""

    def __init__(
        self,
        repository: TrainingRepository,
        storage: TrainingStorage,
        model_repository: Repository,
        manager: TrainingManager,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._model_repository = model_repository
        self._manager = manager
        self._settings = settings

    def create(
        self,
        *,
        created_by: int,
        request: TrainingRequest | Mapping[str, object],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = self._request(request)
        key = self._idempotency_key(idempotency_key)
        self._require_dataset()
        run_key = self._storage.create_run()
        run_dir = self._storage.run_path(run_key)
        manifest = self._manifest(normalized, run_dir)
        try:
            atomic_write_json(run_dir / "manifest.json", manifest.to_json())
            job = self._repository.create_job(
                {
                    "created_by": created_by,
                    "executor": normalized.executor,
                    "base_model": normalized.base_model,
                    "dataset_key": "configured",
                    "epochs": normalized.epochs,
                    "imgsz": normalized.imgsz,
                    "batch": normalized.batch,
                    "device": normalized.device,
                    "patience": normalized.patience,
                    "run_key": run_key,
                    "idempotency_key": key,
                }
            )
        except Exception as exc:
            self._remove_run(run_key)
            raise TrainingServiceError(
                503,
                "training_create_failed",
                "The training job could not be created.",
            ) from exc
        if job["run_key"] != run_key:
            self._remove_run(run_key)
            expected = {
                "created_by": created_by,
                "executor": normalized.executor,
                "base_model": normalized.base_model,
                "dataset_key": "configured",
                "epochs": normalized.epochs,
                "imgsz": normalized.imgsz,
                "batch": normalized.batch,
                "device": normalized.device,
                "patience": normalized.patience,
            }
            if any(job.get(field) != value for field, value in expected.items()):
                raise TrainingServiceError(
                    409,
                    "idempotency_conflict",
                    "The idempotency key belongs to a different training request.",
                )
        self._manager.wake()
        return job

    def list_jobs(
        self, *, page: int = 1, page_size: int = 20, status: str | None = None
    ) -> dict[str, Any]:
        return self._repository.list_jobs(
            page=page, page_size=page_size, status=status
        )

    def get_job(self, job_id: int) -> dict[str, Any]:
        job = self._repository.get_job(job_id)
        if job is None:
            raise self._not_found()
        return job

    def request_stop(self, job_id: int) -> dict[str, Any]:
        if self._repository.get_job(job_id) is None:
            raise self._not_found()
        job = self._repository.request_stop(job_id)
        if job is None:
            raise self._not_found()
        if job["status"] not in {"cancelled", "stopping"}:
            raise TrainingServiceError(
                409,
                "training_job_not_stoppable",
                "The training job cannot be stopped in its current state.",
            )
        self._manager.wake()
        return job

    def resume(self, job_id: int, *, idempotency_key: str) -> dict[str, Any]:
        source = self.get_job(job_id)
        key = self._idempotency_key(idempotency_key)
        if source["status"] not in {"stopped", "interrupted", "failed"}:
            raise TrainingServiceError(
                409,
                "training_job_not_resumable",
                "The training job cannot be resumed in its current state.",
            )
        if not source["has_last"]:
            raise self._resume_unavailable()
        try:
            source_checkpoint = self._storage.resolve_artifact(
                str(source["run_key"]), "last"
            )
            if not source_checkpoint.is_file():
                raise FileNotFoundError("last checkpoint unavailable")
        except (OSError, ValueError) as exc:
            raise self._resume_unavailable() from exc

        run_key = self._storage.create_run()
        run_dir = self._storage.run_path(run_key)
        resume_checkpoint = run_dir / "resume" / "last.pt"
        try:
            resume_checkpoint.parent.mkdir(parents=True)
            shutil.copy2(source_checkpoint, resume_checkpoint)
            normalized = TrainingRequest(
                executor=str(source["executor"]),
                base_model=str(source["base_model"]),
                imgsz=int(source["imgsz"]),
                epochs=int(source["epochs"]),
                batch=str(source["batch"]),
                device=str(source["device"]),
                patience=int(source["patience"]),
            )
            manifest = self._manifest(
                normalized,
                run_dir,
                resume_checkpoint=resume_checkpoint.resolve(),
            )
            atomic_write_json(run_dir / "manifest.json", manifest.to_json())
            resumed = self._repository.create_resume(
                job_id,
                run_key=run_key,
                idempotency_key=key,
            )
        except TrainingServiceError:
            self._remove_run(run_key)
            raise
        except Exception as exc:
            self._remove_run(run_key)
            raise TrainingServiceError(
                503,
                "training_resume_failed",
                "The training resume attempt could not be created.",
            ) from exc
        if resumed["run_key"] != run_key:
            self._remove_run(run_key)
        self._manager.wake()
        return resumed

    def register_model(
        self, job_id: int, *, name: str | None = None
    ) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job["status"] != "completed" or not job["has_best"]:
            raise TrainingServiceError(
                409,
                "training_model_unavailable",
                "A completed training model is required for registration.",
            )
        try:
            best = self._storage.resolve_artifact(str(job["run_key"]), "best")
            if not best.is_file():
                raise FileNotFoundError("best checkpoint unavailable")
            digest = self._sha256(best)
        except (OSError, ValueError) as exc:
            raise TrainingServiceError(
                409,
                "training_model_unavailable",
                "The completed training model is unavailable.",
            ) from exc
        if digest != job["best_sha256"]:
            raise TrainingServiceError(
                409,
                "model_artifact_changed",
                "The evaluated model artifact has changed.",
            )
        model_name = (name or f"{job['base_model']} training {job_id}").strip()
        if not model_name or len(model_name) > 200 or any(
            character in model_name for character in "\r\n\x00"
        ):
            raise TrainingServiceError(
                422, "invalid_model_name", "The model name is invalid."
            )
        metrics = job["metrics"] or {}
        classes = self._require_dataset()["classes"]
        provenance = {
            "training_job_id": job_id,
            "source": "training",
            "base_model": job["base_model"],
            "imgsz": job["imgsz"],
            "metrics_json": json.dumps(
                metrics,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ),
            "deployment_runtime": None,
        }
        try:
            created = self._model_repository.create_model(
                {
                    "name": model_name,
                    "file_path": str(best.resolve()),
                    "runtime": "pt",
                    "classes": classes,
                    "map50": job["map50"],
                    "is_active": False,
                    **provenance,
                }
            )
        except Exception as exc:
            raise TrainingServiceError(
                503,
                "model_registration_failed",
                "The candidate model could not be registered.",
            ) from exc
        return public_model({**created, **provenance})

    def _manifest(
        self,
        request: TrainingRequest,
        run_dir: Path,
        *,
        resume_checkpoint: Path | None = None,
    ) -> TrainingManifest:
        return TrainingManifest.from_json(
            {
                "base_model": request.base_model,
                "dataset_yaml": str(self._storage.dataset_yaml),
                "run_dir": str(run_dir.resolve()),
                "model_cache_dir": str(self._storage.model_cache_root),
                "epochs": request.epochs,
                "imgsz": request.imgsz,
                "batch": request.batch,
                "device": request.device,
                "patience": request.patience,
                **(
                    {"resume_checkpoint": str(resume_checkpoint)}
                    if resume_checkpoint is not None
                    else {}
                ),
            }
        )

    def _request(
        self, request: TrainingRequest | Mapping[str, object]
    ) -> TrainingRequest:
        if isinstance(request, TrainingRequest):
            return request
        try:
            return TrainingRequest(**dict(request))
        except (TypeError, ValueError) as exc:
            executor = request.get("executor") if isinstance(request, Mapping) else None
            code = (
                "executor_unavailable"
                if executor != "local"
                else "invalid_training_request"
            )
            message = (
                "The selected training executor is unavailable."
                if code == "executor_unavailable"
                else "The training request is invalid."
            )
            raise TrainingServiceError(422, code, message) from exc

    def _require_dataset(self) -> dict[str, Any]:
        dataset = training_catalog(self._settings)["dataset"]
        if not dataset["available"] or not dataset["has_test_split"]:
            raise TrainingServiceError(
                409,
                "dataset_unavailable",
                "The configured training dataset is unavailable.",
            )
        return dataset

    @staticmethod
    def _idempotency_key(value: str) -> str:
        if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
            raise TrainingServiceError(
                422,
                "invalid_idempotency_key",
                "A valid idempotency key is required.",
            )
        return value

    def _remove_run(self, run_key: str) -> None:
        try:
            run_dir = self._storage.run_path(run_key)
            shutil.rmtree(run_dir)
        except FileNotFoundError:
            pass

    @staticmethod
    def _not_found() -> TrainingServiceError:
        return TrainingServiceError(
            404, "training_job_not_found", "The training job was not found."
        )

    @staticmethod
    def _resume_unavailable() -> TrainingServiceError:
        return TrainingServiceError(
            409,
            "resume_checkpoint_unavailable",
            "The training resume checkpoint is unavailable.",
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
