from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.routing import APIRoute

from backend.audit import audit_service, audited_admin, request_ip
from backend.auth import AuthenticatedUser
from backend.auth.dependencies import require_same_origin
from backend.schemas import (
    AnnotationSaveRequest,
    DatasetClassCreateRequest,
    DatasetClassRenameRequest,
    DatasetPublishRequest,
    DatasetRescanRequest,
    RevisionRequest,
    TrainingDatasetCreate,
    TrainingDatasetUpdate,
    TrainingJobCreate,
    TrainingModelRegister,
)
from backend.training.annotation_service import AnnotationService, AnnotationServiceError
from backend.training.dataset_validation import DatasetValidationError, DatasetValidator
from backend.training.catalog import TrainingRequest, training_catalog
from backend.training.dataset_service import DatasetService, DatasetServiceError
from backend.training.service import TrainingService, TrainingServiceError
from backend.training.storage import TrainingStorage


class StableTrainingRoute(APIRoute):
    """Keep validation diagnostics stable and free of submitted values."""

    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await route_handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={
                        "code": "invalid_training_request",
                        "message": "The training request is invalid.",
                    },
                )

        return handler


router = APIRouter(
    prefix="/api",
    tags=["training"],
    dependencies=[Depends(require_same_origin)],
    route_class=StableTrainingRoute,
)

_SAFE_JOB_FIELDS = (
    "id", "created_by", "executor", "base_model", "dataset_key",
    "dataset_classes", "epochs", "imgsz", "batch", "device", "patience",
    "status", "current_epoch", "total_epochs", "started_at", "finished_at",
    "stop_requested", "has_best", "has_last", "has_metrics", "precision",
    "recall", "map50", "map50_95", "no_helmet_precision",
    "no_helmet_recall", "accepted", "metrics", "error_code", "error_message",
    "resume_from_id", "created_at", "updated_at",
)
_PUBLIC_ARTIFACTS = frozenset({"log", "confusion_matrix"})


def _service(request: Request) -> TrainingService:
    return request.app.state.training_service


def _storage(request: Request) -> TrainingStorage:
    return request.app.state.training_storage


def _dataset_service(request: Request) -> DatasetService:
    return request.app.state.training_dataset_service


def _annotation_service(request: Request) -> AnnotationService:
    return request.app.state.annotation_service


def _validator(request: Request) -> DatasetValidator:
    return request.app.state.dataset_validator


def _error(error: TrainingServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": error.message},
    )


def _public_job(job: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    result = {key: job.get(key) for key in _SAFE_JOB_FIELDS}
    job_id = result["id"]
    result["progress"] = (
        min(1.0, float(result["current_epoch"]) / float(result["total_epochs"]))
        if result.get("total_epochs")
        else 0.0
    )
    if detail:
        base = f"/api/training-jobs/{job_id}/artifacts"
        result["log_url"] = f"{base}/log"
        result["confusion_matrix_url"] = (
            f"{base}/confusion_matrix" if result.get("has_metrics") else None
        )
    return result


def _record(
    request: Request,
    user: AuthenticatedUser,
    action: str,
    resource_id: int | None,
    outcome: str,
    *,
    details: dict[str, Any] | None = None,
    resource: str = "training_job",
) -> None:
    audit_service(request).record(
        user.id,
        action,
        resource,
        resource_id,
        outcome,
        request_ip(request),
        details=details,
    )


def _request(payload: TrainingJobCreate) -> TrainingRequest:
    if payload.executor != "local":
        raise TrainingServiceError(
            422, "executor_unavailable", "Executor unavailable."
        )
    try:
        return TrainingRequest(**payload.model_dump())
    except (TypeError, ValueError) as exc:
        raise TrainingServiceError(
            422, "invalid_training_request", "The training request is invalid."
        ) from exc


@router.get("/training/catalog")
def get_training_catalog(
    request: Request,
    _user: AuthenticatedUser = Depends(audited_admin("read", "training_catalog")),
) -> dict[str, Any]:
    return training_catalog(
        request.app.state.settings, _dataset_service(request)
    )


def _dataset_error(error: DatasetServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": "The training dataset request failed."},
    )


def _annotation_error(error: Exception) -> JSONResponse:
    code = getattr(error, "code", "dataset_invalid")
    statuses = {
        "dataset_not_found": 404, "annotation_not_found": 404,
        "dataset_published": 409, "annotation_conflict": 409,
        "class_in_use": 409, "class_order_locked": 409,
        "dataset_conflict": 409, "dataset_validation_failed": 409,
        "dataset_warnings_unconfirmed": 409,
        "annotation_invalid": 422, "class_invalid": 422,
        "annotation_write_failed": 503,
    }
    return JSONResponse(
        status_code=statuses.get(code, 422),
        content={"code": code, "message": "The dataset annotation request failed."},
    )


@router.get("/training/dataset-roots")
def list_training_dataset_roots(
    request: Request,
    _user: AuthenticatedUser = Depends(audited_admin("read", "training_dataset")),
):
    return {"items": _dataset_service(request).list_roots()}


@router.get("/training/dataset-directories")
def list_training_dataset_directories(
    request: Request,
    root_alias: str = Query(min_length=1, max_length=64),
    path: str = Query(default="", max_length=512),
    _user: AuthenticatedUser = Depends(audited_admin("read", "training_dataset")),
):
    try:
        return {"items": _dataset_service(request).list_directories(root_alias, path)}
    except DatasetServiceError as exc:
        return _dataset_error(exc)


@router.get("/training/datasets")
def list_training_datasets(
    request: Request,
    _user: AuthenticatedUser = Depends(audited_admin("read", "training_dataset")),
):
    return {"items": _dataset_service(request).catalog()}


@router.post("/training/datasets", status_code=status.HTTP_201_CREATED)
def create_training_dataset(
    payload: TrainingDatasetCreate,
    request: Request,
    user: AuthenticatedUser = Depends(
        audited_admin("training_dataset_create", "training_dataset")
    ),
):
    try:
        item = _dataset_service(request).create(
            payload.model_dump(), created_by=user.id
        )
    except DatasetServiceError as exc:
        _record(request, user, "training_dataset_create", None, "rejected", details={"code": exc.code}, resource="training_dataset")
        return _dataset_error(exc)
    _record(request, user, "training_dataset_create", item["id"], "success", resource="training_dataset")
    return item


@router.patch("/training/datasets/{dataset_id}")
def update_training_dataset(
    dataset_id: int,
    payload: TrainingDatasetUpdate,
    request: Request,
    user: AuthenticatedUser = Depends(
        audited_admin("training_dataset_update", "training_dataset")
    ),
):
    try:
        item = _dataset_service(request).update(
            dataset_id, payload.model_dump(exclude_none=True)
        )
    except DatasetServiceError as exc:
        _record(request, user, "training_dataset_update", dataset_id, "rejected", details={"code": exc.code}, resource="training_dataset")
        return _dataset_error(exc)
    _record(request, user, "training_dataset_update", dataset_id, "success", resource="training_dataset")
    return item


@router.delete("/training/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def disable_training_dataset(
    dataset_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(
        audited_admin("training_dataset_disable", "training_dataset")
    ),
):
    try:
        _dataset_service(request).disable(dataset_id)
    except DatasetServiceError as exc:
        _record(request, user, "training_dataset_disable", dataset_id, "rejected", details={"code": exc.code}, resource="training_dataset")
        return _dataset_error(exc)
    _record(request, user, "training_dataset_disable", dataset_id, "success", resource="training_dataset")
    return None


@router.post(
    "/training/datasets/{dataset_id}/versions",
    status_code=status.HTTP_201_CREATED,
)
def clone_training_dataset_version(
    dataset_id: int, request: Request,
    user: AuthenticatedUser = Depends(audited_admin("dataset_clone", "training_dataset")),
):
    try:
        item = _dataset_service(request).clone_version(dataset_id, created_by=user.id)
    except DatasetServiceError as exc:
        _record(request, user, "dataset_clone", dataset_id, "rejected", details={"code": exc.code}, resource="training_dataset")
        return _dataset_error(exc)
    _record(request, user, "dataset_clone", item["id"], "success", resource="training_dataset")
    return item


@router.get("/training/datasets/{dataset_id}/versions")
def list_training_dataset_versions(
    dataset_id: int, request: Request,
    _user: AuthenticatedUser = Depends(audited_admin("read", "training_dataset")),
):
    try:
        return {"items": _dataset_service(request).list_versions(dataset_id)}
    except DatasetServiceError as exc:
        return _dataset_error(exc)


@router.post("/training/datasets/{dataset_id}/rescan")
def rescan_training_dataset(
    dataset_id: int, payload: DatasetRescanRequest, request: Request,
    user: AuthenticatedUser = Depends(audited_admin("dataset_rescan", "training_dataset")),
):
    try:
        result = _dataset_service(request).rescan(dataset_id, payload.split)
    except DatasetServiceError as exc:
        _record(request, user, "dataset_rescan", dataset_id, "rejected", details={"code": exc.code}, resource="training_dataset")
        return _dataset_error(exc)
    _record(request, user, "dataset_rescan", dataset_id, "success", resource="training_dataset")
    return result


@router.get("/training/datasets/{dataset_id}/images")
def list_training_dataset_images(
    dataset_id: int, request: Request,
    split: str = Query(pattern="^(train|val|test)$"),
    review_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    _user: AuthenticatedUser = Depends(audited_admin("read", "training_dataset_image")),
):
    try:
        return _dataset_service(request).list_images(
            dataset_id, split=split, status=review_status, page=page, page_size=page_size
        )
    except DatasetServiceError as exc:
        return _dataset_error(exc)


@router.get("/training/datasets/{dataset_id}/images/{image_id}")
def get_training_dataset_image(
    dataset_id: int, image_id: int, request: Request,
    _user: AuthenticatedUser = Depends(audited_admin("read", "training_dataset_image")),
):
    try:
        return _annotation_service(request).get_image(dataset_id, image_id)
    except AnnotationServiceError as exc:
        return _annotation_error(exc)


@router.get("/training/datasets/{dataset_id}/images/{image_id}/content")
def get_training_dataset_image_content(
    dataset_id: int, image_id: int, request: Request,
    _user: AuthenticatedUser = Depends(audited_admin("read", "training_dataset_image")),
):
    try:
        path, media_type = _annotation_service(request).content(dataset_id, image_id)
    except AnnotationServiceError as exc:
        return _annotation_error(exc)
    return FileResponse(
        path, media_type=media_type,
        headers={
            "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


def _annotation_mutation(request, user, action, dataset_id, callback):
    try:
        result = callback()
    except (AnnotationServiceError, DatasetValidationError) as exc:
        _record(request, user, action, dataset_id, "rejected", details={"code": exc.code}, resource="training_dataset")
        return _annotation_error(exc)
    _record(request, user, action, dataset_id, "success", resource="training_dataset")
    return result


@router.put("/training/datasets/{dataset_id}/images/{image_id}/annotations")
def save_training_dataset_annotations(
    dataset_id: int, image_id: int, payload: AnnotationSaveRequest, request: Request,
    user: AuthenticatedUser = Depends(audited_admin("annotation_save", "training_dataset_image")),
):
    return _annotation_mutation(
        request, user, "annotation_save", dataset_id,
        lambda: _annotation_service(request).save(
            dataset_id, image_id, payload.revision,
            [box.model_dump() for box in payload.boxes],
        ),
    )


@router.post("/training/datasets/{dataset_id}/images/{image_id}/confirm-empty")
def confirm_training_dataset_image_empty(
    dataset_id: int, image_id: int, payload: RevisionRequest, request: Request,
    user: AuthenticatedUser = Depends(audited_admin("annotation_confirm_empty", "training_dataset_image")),
):
    return _annotation_mutation(
        request, user, "annotation_confirm_empty", dataset_id,
        lambda: _annotation_service(request).confirm_empty(dataset_id, image_id, payload.revision),
    )


@router.post("/training/datasets/{dataset_id}/classes")
def add_training_dataset_class(
    dataset_id: int, payload: DatasetClassCreateRequest, request: Request,
    user: AuthenticatedUser = Depends(audited_admin("dataset_class_add", "training_dataset")),
):
    return _annotation_mutation(request, user, "dataset_class_add", dataset_id, lambda: _annotation_service(request).add_class(dataset_id, payload.revision, payload.name))


@router.patch("/training/datasets/{dataset_id}/classes/{class_id}")
def rename_training_dataset_class(
    dataset_id: int, class_id: int, payload: DatasetClassRenameRequest, request: Request,
    user: AuthenticatedUser = Depends(audited_admin("dataset_class_rename", "training_dataset")),
):
    return _annotation_mutation(request, user, "dataset_class_rename", dataset_id, lambda: _annotation_service(request).rename_class(dataset_id, payload.revision, class_id, payload.name))


@router.delete("/training/datasets/{dataset_id}/classes/{class_id}")
def delete_training_dataset_class(
    dataset_id: int, class_id: int, payload: RevisionRequest, request: Request,
    user: AuthenticatedUser = Depends(audited_admin("dataset_class_delete", "training_dataset")),
):
    return _annotation_mutation(request, user, "dataset_class_delete", dataset_id, lambda: _annotation_service(request).delete_class(dataset_id, payload.revision, class_id))


@router.post("/training/datasets/{dataset_id}/validate")
def validate_training_dataset(
    dataset_id: int, request: Request,
    user: AuthenticatedUser = Depends(audited_admin("dataset_validate", "training_dataset")),
):
    return _annotation_mutation(request, user, "dataset_validate", dataset_id, lambda: _validator(request).validate(dataset_id).to_json())


@router.post("/training/datasets/{dataset_id}/publish")
def publish_training_dataset(
    dataset_id: int, payload: DatasetPublishRequest, request: Request,
    user: AuthenticatedUser = Depends(audited_admin("dataset_publish", "training_dataset")),
):
    return _annotation_mutation(request, user, "dataset_publish", dataset_id, lambda: _validator(request).publish(dataset_id, payload.revision, payload.confirm_warnings))


@router.get("/training-jobs")
def list_training_jobs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    _user: AuthenticatedUser = Depends(audited_admin("read", "training_job")),
):
    result = _service(request).list_jobs(
        page=page, page_size=page_size, status=status_filter
    )
    return {
        **result,
        "items": [_public_job(job) for job in result["items"]],
    }


@router.post("/training-jobs", status_code=status.HTTP_201_CREATED)
def create_training_job(
    payload: TrainingJobCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=64),
    user: AuthenticatedUser = Depends(audited_admin("training_create", "training_job")),
):
    try:
        if not request.app.state.settings.training_enabled:
            raise TrainingServiceError(503, "training_disabled", "Local training is disabled.")
        job = _service(request).create(
            created_by=user.id,
            request=_request(payload),
            idempotency_key=idempotency_key,
        )
    except TrainingServiceError as exc:
        _record(request, user, "training_create", None, "rejected", details={"code": exc.code})
        return _error(exc)
    _record(request, user, "training_create", job["id"], "success")
    return _public_job(job, detail=True)


@router.get("/training-jobs/{job_id}")
def get_training_job(
    job_id: int,
    request: Request,
    _user: AuthenticatedUser = Depends(audited_admin("read", "training_job")),
):
    try:
        return _public_job(_service(request).get_job(job_id), detail=True)
    except TrainingServiceError as exc:
        return _error(exc)


@router.delete("/training-jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_training_job(
    job_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(audited_admin("training_delete", "training_job")),
):
    try:
        _service(request).delete(job_id)
    except TrainingServiceError as exc:
        _record(request, user, "training_delete", job_id, "rejected", details={"code": exc.code})
        return _error(exc)
    _record(request, user, "training_delete", job_id, "success")
    return None


def _mutate(
    request: Request,
    user: AuthenticatedUser,
    action: str,
    job_id: int,
    callback,
):
    try:
        result = callback()
    except TrainingServiceError as exc:
        _record(request, user, action, job_id, "rejected", details={"code": exc.code})
        return _error(exc)
    _record(request, user, action, job_id, "success")
    return _public_job(result, detail=True) if "status" in result else result


@router.post("/training-jobs/{job_id}/stop")
def stop_training_job(
    job_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(audited_admin("training_stop", "training_job")),
):
    return _mutate(request, user, "training_stop", job_id, lambda: _service(request).request_stop(job_id))


@router.post("/training-jobs/{job_id}/resume", status_code=status.HTTP_201_CREATED)
def resume_training_job(
    job_id: int,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=64),
    user: AuthenticatedUser = Depends(audited_admin("training_resume", "training_job")),
):
    return _mutate(
        request, user, "training_resume", job_id,
        lambda: _service(request).resume(job_id, idempotency_key=idempotency_key),
    )


@router.post("/training-jobs/{job_id}/register-model", status_code=status.HTTP_201_CREATED)
def register_training_model(
    job_id: int,
    request: Request,
    payload: TrainingModelRegister | None = None,
    user: AuthenticatedUser = Depends(audited_admin("training_register", "training_job")),
):
    return _mutate(
        request, user, "training_register", job_id,
        lambda: _service(request).register_model(job_id, name=payload.name if payload else None),
    )


@router.get("/training-jobs/{job_id}/artifacts/{kind}")
def get_training_artifact(
    job_id: int,
    kind: str,
    request: Request,
    _user: AuthenticatedUser = Depends(audited_admin("read", "training_artifact")),
):
    if kind not in _PUBLIC_ARTIFACTS:
        return JSONResponse(status_code=404, content={"code": "artifact_not_found", "message": "The artifact was not found."})
    try:
        job = _service(request).get_job(job_id)
        storage = _storage(request)
        if kind == "log":
            return PlainTextResponse(
                storage.tail_log(str(job["run_key"]), storage.log_tail_bytes),
                headers={"X-Content-Type-Options": "nosniff"},
            )
        path = storage.resolve_artifact(str(job["run_key"]), kind)
        if not path.is_file():
            raise FileNotFoundError
    except TrainingServiceError as exc:
        return _error(exc)
    except (OSError, ValueError):
        return JSONResponse(status_code=404, content={"code": "artifact_not_found", "message": "The artifact was not found."})
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
    )
