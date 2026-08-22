from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.routing import APIRoute

from backend.audit import audit_service, audited_admin, request_ip
from backend.auth import AuthenticatedUser
from backend.auth.dependencies import require_same_origin
from backend.schemas import TrainingJobCreate, TrainingModelRegister
from backend.training.catalog import TrainingRequest, training_catalog
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
) -> None:
    audit_service(request).record(
        user.id,
        action,
        "training_job",
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
    return training_catalog(request.app.state.settings)


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
