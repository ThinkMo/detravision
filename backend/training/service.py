from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from backend.core.config import Settings
from backend.core.public_records import public_model
from backend.db.repositories import Repository
from training.contracts import TrainingManifest, atomic_write_json
from training.configuration import (
    CONFIG_SCHEMA_VERSION,
    TrainingConfigError,
    normalize_acceptance_config,
)

from .catalog import TrainingRequest
from .dataset_service import DatasetService, DatasetServiceError
from .repository import TrainingRepository
from .storage import TrainingStorage


_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")


class TrainingManager(Protocol):
    def wake(self) -> None: ...
    def owns_job(self, job_id: int) -> bool: ...


def _checkpoint_classes(path: Path) -> list[str]:
    """Read class metadata from a service-owned Ultralytics checkpoint."""
    from ultralytics import YOLO

    names = getattr(YOLO(str(path)), "names", None)
    if isinstance(names, Mapping):
        indexed = sorted((int(key), value) for key, value in names.items())
        if [index for index, _value in indexed] != list(range(len(indexed))):
            raise ValueError("checkpoint class indexes are not contiguous")
        values = [value for _index, value in indexed]
    elif isinstance(names, Sequence) and not isinstance(names, (str, bytes)):
        values = list(names)
    else:
        raise ValueError("checkpoint class metadata is unavailable")
    if not values or not all(isinstance(value, str) and value for value in values):
        raise ValueError("checkpoint class metadata is invalid")
    return values


@dataclass(slots=True)
class TrainingServiceError(Exception):
    status_code: int
    code: str
    message: str
    field_errors: dict[str, list[str]] | None = None


class TrainingService:
    """Orchestrate trusted manifests, persisted jobs, and candidate models."""

    def __init__(
        self,
        repository: TrainingRepository,
        storage: TrainingStorage,
        model_repository: Repository,
        manager: TrainingManager,
        settings: Settings,
        dataset_service: DatasetService,
        *,
        checkpoint_class_reader: Callable[[Path], list[str]] = _checkpoint_classes,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._model_repository = model_repository
        self._manager = manager
        self._settings = settings
        self._dataset_service = dataset_service
        self._checkpoint_class_reader = checkpoint_class_reader

    def create(
        self,
        *,
        created_by: int,
        request: TrainingRequest | Mapping[str, object],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = self._request(request)
        key = self._idempotency_key(idempotency_key)
        dataset = self._require_dataset(normalized.dataset_key)
        acceptance_config = self._acceptance(normalized, dataset["classes"])
        self._require_weights(normalized.base_model)
        run_key = self._storage.create_run()
        run_dir = self._storage.run_path(run_key)
        try:
            dataset_yaml = self._storage.write_dataset_snapshot(run_key, dataset)
            manifest = self._manifest(
                normalized,
                run_dir,
                dataset_yaml=dataset_yaml,
                dataset_classes=dataset["classes"],
                acceptance_config=acceptance_config,
            )
            atomic_write_json(run_dir / "manifest.json", manifest.to_json())
            job = self._repository.create_job(
                {
                    "created_by": created_by,
                    "executor": normalized.executor,
                    "base_model": normalized.base_model,
                    "dataset_key": normalized.dataset_key,
                    "training_dataset_id": dataset["training_dataset_id"],
                    "dataset_classes": dataset["classes"],
                    "epochs": normalized.epochs,
                    "imgsz": normalized.imgsz,
                    "batch": normalized.batch,
                    "device": normalized.device,
                    "patience": normalized.patience,
                    "config_schema_version": CONFIG_SCHEMA_VERSION,
                    "training_profile": normalized.profile,
                    "advanced_config": normalized.advanced_config,
                    "acceptance_config": acceptance_config,
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
                "dataset_key": normalized.dataset_key,
                "training_dataset_id": dataset["training_dataset_id"],
                "dataset_classes": dataset["classes"],
                "epochs": normalized.epochs,
                "imgsz": normalized.imgsz,
                "batch": normalized.batch,
                "device": normalized.device,
                "patience": normalized.patience,
                "config_schema_version": CONFIG_SCHEMA_VERSION,
                "training_profile": normalized.profile,
                "advanced_config": normalized.advanced_config,
                "acceptance_config": acceptance_config,
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

    def clone_template(self, job_id: int) -> dict[str, Any]:
        source = self.get_job(job_id)
        if source["training_profile"] == "legacy":
            raise TrainingServiceError(
                409,
                "training_schema_unsupported",
                "Legacy training jobs cannot be cloned.",
            )
        return {
            "dataset_key": source["dataset_key"],
            "executor": source["executor"],
            "base_model": source["base_model"],
            "imgsz": source["imgsz"],
            "epochs": source["epochs"],
            "batch": source["batch"],
            "device": source["device"],
            "patience": source["patience"],
            "mode": "advanced",
            "profile": source["training_profile"],
            "advanced_config": source["advanced_config"],
            "acceptance_config": source["acceptance_config"],
        }

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

    def delete(self, job_id: int) -> None:
        job = self.get_job(job_id)
        if job["status"] not in {"stopped", "completed", "failed", "interrupted", "cancelled"} or self._manager.owns_job(job_id):
            raise TrainingServiceError(409, "job_active", "Active training jobs cannot be deleted.")
        if self._model_repository.has_model_for_training_job(job_id):
            raise TrainingServiceError(409, "model_registered", "A registered model still references this training job.")
        try:
            run_dir = self._storage.run_path(str(job["run_key"]))
            shutil.rmtree(run_dir)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            raise TrainingServiceError(503, "cleanup_failed", "Training job cleanup failed.") from exc
        try:
            if not self._repository.delete_terminal(job_id):
                raise TrainingServiceError(409, "job_active", "Active training jobs cannot be deleted.")
        except TrainingServiceError:
            raise
        except Exception as exc:
            raise TrainingServiceError(503, "cleanup_failed", "Training job cleanup failed.") from exc

    def resume(self, job_id: int, *, idempotency_key: str) -> dict[str, Any]:
        source = self.get_job(job_id)
        key = self._idempotency_key(idempotency_key)
        if source["status"] not in {"stopped", "interrupted", "failed"}:
            raise TrainingServiceError(
                409,
                "training_job_not_resumable",
                "The training job cannot be resumed in its current state.",
            )
        if (
            source["status"] == "failed"
            and source["error_code"] == "evaluation_failed"
            and int(source["current_epoch"]) >= int(source["total_epochs"])
        ):
            raise TrainingServiceError(
                409,
                "evaluation_retry_required",
                "Evaluation must be retried without resuming completed training.",
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
                dataset_key=str(source["dataset_key"]),
                executor=str(source["executor"]),
                base_model=str(source["base_model"]),
                imgsz=int(source["imgsz"]),
                epochs=int(source["epochs"]),
                batch=str(source["batch"]),
                device=str(source["device"]),
                patience=int(source["patience"]),
                mode="advanced",
                profile=(
                    str(source["training_profile"])
                    if source["training_profile"] != "legacy"
                    else "custom"
                ),
                advanced_config=source["advanced_config"],
                acceptance_config=source["acceptance_config"],
            )
            try:
                source_run = self._storage.run_path(str(source["run_key"]))
                source_dataset = (source_run / "dataset.yaml").resolve()
                source_dataset.relative_to(source_run)
                if not source_dataset.is_file():
                    raise FileNotFoundError("dataset snapshot unavailable")
                dataset_yaml = run_dir / "dataset.yaml"
                shutil.copy2(source_dataset, dataset_yaml)
            except (OSError, ValueError) as exc:
                raise TrainingServiceError(
                    409, "dataset_unavailable", "The training dataset is unavailable."
                ) from exc
            manifest = self._manifest(
                normalized,
                run_dir,
                dataset_yaml=dataset_yaml.resolve(),
                dataset_classes=source["dataset_classes"],
                acceptance_config=source["acceptance_config"],
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
            expected = {
                "created_by": source["created_by"],
                "executor": source["executor"],
                "base_model": source["base_model"],
                "dataset_key": source["dataset_key"],
                "dataset_classes": source["dataset_classes"],
                "epochs": source["epochs"],
                "imgsz": source["imgsz"],
                "batch": source["batch"],
                "device": source["device"],
                "patience": source["patience"],
                "resume_from_id": source["id"],
                "config_schema_version": source["config_schema_version"],
                "training_profile": source["training_profile"],
                "advanced_config": source["advanced_config"],
                "acceptance_config": source["acceptance_config"],
            }
            if any(
                resumed.get(field) != value for field, value in expected.items()
            ):
                raise TrainingServiceError(
                    409,
                    "idempotency_conflict",
                    "The idempotency key belongs to a different training request.",
                )
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
        classes = job.get("dataset_classes")
        if not isinstance(classes, list) or not classes:
            raise TrainingServiceError(
                409,
                "training_model_unavailable",
                "The training dataset class snapshot is unavailable.",
            )
        try:
            checkpoint_classes = self._checkpoint_class_reader(best.resolve())
        except Exception as exc:
            raise TrainingServiceError(
                409,
                "model_classes_mismatch",
                "The model classes do not match the training dataset snapshot.",
            ) from exc
        if checkpoint_classes != classes:
            raise TrainingServiceError(
                409,
                "model_classes_mismatch",
                "The model classes do not match the training dataset snapshot.",
            )
        training_config = {
            "schema_version": job["config_schema_version"],
            "profile": job["training_profile"],
            "advanced_config": job["advanced_config"],
            "acceptance_config": job["acceptance_config"],
        }
        stored_metrics = {
            **metrics,
            "_provenance": {
                "training_profile": job["training_profile"],
                "training_config": training_config,
                "dataset_manifest_digest": job.get("dataset_manifest_digest"),
                "accepted": job.get("accepted"),
            },
        }
        provenance = {
            "training_job_id": job_id,
            "source": "training",
            "base_model": job["base_model"],
            "imgsz": job["imgsz"],
            "metrics_json": json.dumps(
                stored_metrics,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ),
            "deployment_runtime": None,
        }
        try:
            created = self._model_repository.create_trained_model(
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
        dataset_yaml: Path,
        dataset_classes: list[str],
        acceptance_config: Mapping[str, object],
        resume_checkpoint: Path | None = None,
    ) -> TrainingManifest:
        return TrainingManifest.from_json(
            {
                "base_model": request.base_model,
                "dataset_yaml": str(dataset_yaml.resolve()),
                "run_dir": str(run_dir.resolve()),
                "model_cache_dir": str(self._storage.model_cache_root),
                "epochs": request.epochs,
                "imgsz": request.imgsz,
                "batch": request.batch,
                "device": request.device,
                "patience": request.patience,
                "config_schema_version": CONFIG_SCHEMA_VERSION,
                "dataset_classes": list(dataset_classes),
                "advanced_config": dict(request.advanced_config or {}),
                "acceptance_config": dict(acceptance_config),
                **(
                    {"resume_checkpoint": str(resume_checkpoint)}
                    if resume_checkpoint is not None
                    else {}
                ),
            }
        )

    @staticmethod
    def _acceptance(
        request: TrainingRequest, classes: list[str]
    ) -> dict[str, object]:
        try:
            return normalize_acceptance_config(
                request.acceptance_config,
                classes=classes,
                template=request.acceptance_template,
            )
        except TrainingConfigError as exc:
            raise TrainingServiceError(
                422,
                exc.code,
                "The training acceptance configuration is invalid.",
                exc.field_errors,
            ) from exc

    def _request(
        self, request: TrainingRequest | Mapping[str, object]
    ) -> TrainingRequest:
        if isinstance(request, TrainingRequest):
            return request
        try:
            return TrainingRequest(**dict(request))
        except TrainingConfigError as exc:
            raise TrainingServiceError(
                422,
                exc.code,
                "The training configuration is invalid.",
                exc.field_errors,
            ) from exc
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

    def _require_dataset(self, key: str) -> dict[str, Any]:
        try:
            return self._dataset_service.resolve(key)
        except DatasetServiceError as exc:
            raise TrainingServiceError(
                409,
                "dataset_unavailable",
                "The selected training dataset is unavailable.",
            ) from exc

    def _require_weights(self, model_id: str) -> None:
        roots = (
            Path(__file__).resolve().parents[2],
            self._storage.model_cache_root,
        )
        for root in roots:
            try:
                candidate = (root / model_id).resolve()
                candidate.relative_to(root)
                if candidate.is_file():
                    return
            except (OSError, ValueError):
                continue
        raise TrainingServiceError(
            409,
            "weights_unavailable",
            "The selected base-model weights are unavailable.",
        )

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
