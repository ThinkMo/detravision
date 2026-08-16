from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError

from backend.audit import audit_service, audited_admin, request_ip
from backend.auth import AuthenticatedUser
from backend.auth.dependencies import current_user, require_same_origin
from backend.db import Repository
from backend.schemas import (
    AlarmGroupCreate,
    CameraCreate,
    CameraUpdate,
    EventUpdate,
    ModelCreate,
    PersonCreate,
    PersonUpdate,
)

router = APIRouter(prefix="/api")
protected_router = APIRouter(
    dependencies=[Depends(require_same_origin), Depends(current_user)],
)


def repository(request: Request) -> Repository:
    return request.app.state.repository


def require(item: dict[str, Any] | None) -> dict[str, Any]:
    if item is None:
        raise HTTPException(status_code=404, detail="resource not found")
    return item


def create_or_conflict(create: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return create()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="resource conflict") from exc


def record_success(
    request: Request,
    user: AuthenticatedUser,
    action: str,
    resource_type: str,
    resource_id: int | str | None,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    audit_service(request).record(
        user.id,
        action,
        resource_type,
        resource_id,
        "success",
        request_ip(request),
        details=details,
    )


@contextmanager
def audit_rejections(
    request: Request,
    user: AuthenticatedUser,
    action: str,
    resource_type: str,
    resource_id: int | str | None,
):
    try:
        yield
    except HTTPException as exc:
        audit_service(request).record(
            user.id,
            action,
            resource_type,
            resource_id,
            "rejected",
            request_ip(request),
            details={"status_code": exc.status_code},
        )
        raise


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    runtime = request.app.state.runtime
    return {"status": "ok", "runtime": runtime.status()}


@protected_router.get("/cameras")
def cameras(db: Repository = Depends(repository)) -> list[dict[str, Any]]:
    return db.list_cameras()


@protected_router.post("/cameras", status_code=status.HTTP_201_CREATED)
def create_camera(
    payload: CameraCreate,
    request: Request,
    db: Repository = Depends(repository),
    user: AuthenticatedUser = Depends(audited_admin("create", "camera")),
) -> dict[str, Any]:
    with audit_rejections(request, user, "create", "camera", None):
        camera = create_or_conflict(lambda: db.create_camera(payload.model_dump()))
    record_success(request, user, "create", "camera", camera["id"])
    return camera


@protected_router.patch("/cameras/{camera_id}")
def update_camera(
    camera_id: int,
    payload: CameraUpdate,
    request: Request,
    db: Repository = Depends(repository),
    user: AuthenticatedUser = Depends(audited_admin("update", "camera")),
) -> dict[str, Any]:
    with audit_rejections(request, user, "update", "camera", camera_id):
        camera = require(
            db.update_camera(camera_id, payload.model_dump(exclude_unset=True))
        )
    record_success(request, user, "update", "camera", camera_id)
    return camera


@protected_router.delete("/cameras/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(
    camera_id: int,
    request: Request,
    db: Repository = Depends(repository),
    user: AuthenticatedUser = Depends(audited_admin("delete", "camera")),
) -> None:
    with audit_rejections(request, user, "delete", "camera", camera_id):
        if not db.delete_camera(camera_id):
            raise HTTPException(status_code=404, detail="camera not found")
    record_success(request, user, "delete", "camera", camera_id)


@protected_router.get("/persons")
def persons(db: Repository = Depends(repository)) -> list[dict[str, Any]]:
    return db.list_persons()


@protected_router.post("/persons", status_code=status.HTTP_201_CREATED)
def create_person(
    payload: PersonCreate,
    request: Request,
    db: Repository = Depends(repository),
    user: AuthenticatedUser = Depends(audited_admin("create", "person")),
) -> dict[str, Any]:
    with audit_rejections(request, user, "create", "person", None):
        person = create_or_conflict(lambda: db.create_person(payload.model_dump()))
    record_success(request, user, "create", "person", person["id"])
    return person


@protected_router.patch("/persons/{person_id}")
def update_person(
    person_id: int,
    payload: PersonUpdate,
    request: Request,
    db: Repository = Depends(repository),
    user: AuthenticatedUser = Depends(audited_admin("update", "person")),
) -> dict[str, Any]:
    with audit_rejections(request, user, "update", "person", person_id):
        person = require(
            db.update_person(person_id, payload.model_dump(exclude_unset=True))
        )
    record_success(request, user, "update", "person", person_id)
    return person


@protected_router.delete("/persons/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_person(
    person_id: int,
    request: Request,
    db: Repository = Depends(repository),
    user: AuthenticatedUser = Depends(audited_admin("delete", "person")),
) -> None:
    with audit_rejections(request, user, "delete", "person", person_id):
        if not db.delete_person(person_id):
            raise HTTPException(status_code=404, detail="person not found")
    record_success(request, user, "delete", "person", person_id)


@protected_router.get("/alarm-groups")
def alarm_groups(db: Repository = Depends(repository)) -> list[dict[str, Any]]:
    groups = db.list_alarm_groups()
    for group in groups:
        group["members"] = db.group_members(group["id"])
    return groups


@protected_router.post("/alarm-groups", status_code=status.HTTP_201_CREATED)
def create_alarm_group(
    payload: AlarmGroupCreate,
    request: Request,
    db: Repository = Depends(repository),
    user: AuthenticatedUser = Depends(audited_admin("create", "alarm_group")),
) -> dict[str, Any]:
    with audit_rejections(request, user, "create", "alarm_group", None):
        group = create_or_conflict(
            lambda: db.create_alarm_group({"name": payload.name})
        )
        try:
            db.replace_group_members(group["id"], payload.person_ids)
        except IntegrityError as exc:
            db.delete_alarm_group(group["id"])
            raise HTTPException(
                status_code=409,
                detail="person id does not exist",
            ) from exc
        group["members"] = db.group_members(group["id"])
    record_success(request, user, "create", "alarm_group", group["id"])
    return group


@protected_router.get("/events")
def events(
    camera_id: int | None = None,
    event_type: str | None = None,
    event_status: str | None = Query(default=None, alias="status"),
    db: Repository = Depends(repository),
) -> list[dict[str, Any]]:
    return db.list_events(camera_id=camera_id, event_type=event_type, status=event_status)


@protected_router.patch("/events/{event_id}")
def update_event(
    event_id: int,
    payload: EventUpdate,
    request: Request,
    db: Repository = Depends(repository),
    user: AuthenticatedUser = Depends(current_user),
) -> dict[str, Any]:
    changes: dict[str, Any] = {"status": payload.status}
    if payload.status in {"ack", "closed"}:
        changes["ack_user_id"] = user.id
    with audit_rejections(request, user, "update", "event", event_id):
        event = require(db.update_event(event_id, changes))
    record_success(
        request,
        user,
        "update",
        "event",
        event_id,
        details={"status": payload.status},
    )
    return event


@protected_router.get("/models")
def models(db: Repository = Depends(repository)) -> list[dict[str, Any]]:
    return db.list_models()


@protected_router.post("/models", status_code=status.HTTP_201_CREATED)
def create_model(
    payload: ModelCreate,
    request: Request,
    db: Repository = Depends(repository),
    user: AuthenticatedUser = Depends(audited_admin("create", "model")),
) -> dict[str, Any]:
    with audit_rejections(request, user, "create", "model", None):
        model = create_or_conflict(lambda: db.create_model(payload.model_dump()))
    record_success(request, user, "create", "model", model["id"])
    return model


@protected_router.post("/models/{model_id}/activate")
def activate_model(
    model_id: int,
    request: Request,
    db: Repository = Depends(repository),
    user: AuthenticatedUser = Depends(audited_admin("activate", "model")),
) -> dict[str, Any]:
    with audit_rejections(request, user, "activate", "model", model_id):
        model = require(db.activate_model(model_id))
    record_success(request, user, "activate", "model", model_id)
    return model


router.include_router(protected_router)
