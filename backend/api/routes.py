from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError

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
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    runtime = request.app.state.runtime
    return {"status": "ok", "runtime": runtime.status()}


@router.get("/cameras")
def cameras(db: Repository = Depends(repository)) -> list[dict[str, Any]]:
    return db.list_cameras()


@router.post("/cameras", status_code=status.HTTP_201_CREATED)
def create_camera(payload: CameraCreate, db: Repository = Depends(repository)) -> dict[str, Any]:
    return create_or_conflict(lambda: db.create_camera(payload.model_dump()))


@router.patch("/cameras/{camera_id}")
def update_camera(camera_id: int, payload: CameraUpdate, db: Repository = Depends(repository)) -> dict[str, Any]:
    return require(db.update_camera(camera_id, payload.model_dump(exclude_unset=True)))


@router.delete("/cameras/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: int, db: Repository = Depends(repository)) -> None:
    if not db.delete_camera(camera_id):
        raise HTTPException(status_code=404, detail="camera not found")


@router.get("/persons")
def persons(db: Repository = Depends(repository)) -> list[dict[str, Any]]:
    return db.list_persons()


@router.post("/persons", status_code=status.HTTP_201_CREATED)
def create_person(payload: PersonCreate, db: Repository = Depends(repository)) -> dict[str, Any]:
    return create_or_conflict(lambda: db.create_person(payload.model_dump()))


@router.patch("/persons/{person_id}")
def update_person(person_id: int, payload: PersonUpdate, db: Repository = Depends(repository)) -> dict[str, Any]:
    return require(db.update_person(person_id, payload.model_dump(exclude_unset=True)))


@router.delete("/persons/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_person(person_id: int, db: Repository = Depends(repository)) -> None:
    if not db.delete_person(person_id):
        raise HTTPException(status_code=404, detail="person not found")


@router.get("/alarm-groups")
def alarm_groups(db: Repository = Depends(repository)) -> list[dict[str, Any]]:
    groups = db.list_alarm_groups()
    for group in groups:
        group["members"] = db.group_members(group["id"])
    return groups


@router.post("/alarm-groups", status_code=status.HTTP_201_CREATED)
def create_alarm_group(payload: AlarmGroupCreate, db: Repository = Depends(repository)) -> dict[str, Any]:
    group = create_or_conflict(lambda: db.create_alarm_group({"name": payload.name}))
    try:
        db.replace_group_members(group["id"], payload.person_ids)
    except IntegrityError as exc:
        db.delete_alarm_group(group["id"])
        raise HTTPException(status_code=409, detail="person id does not exist") from exc
    group["members"] = db.group_members(group["id"])
    return group


@router.get("/events")
def events(
    camera_id: int | None = None,
    event_type: str | None = None,
    event_status: str | None = Query(default=None, alias="status"),
    db: Repository = Depends(repository),
) -> list[dict[str, Any]]:
    return db.list_events(camera_id=camera_id, event_type=event_type, status=event_status)


@router.patch("/events/{event_id}")
def update_event(event_id: int, payload: EventUpdate, db: Repository = Depends(repository)) -> dict[str, Any]:
    return require(db.update_event(event_id, payload.model_dump()))


@router.get("/models")
def models(db: Repository = Depends(repository)) -> list[dict[str, Any]]:
    return db.list_models()


@router.post("/models", status_code=status.HTTP_201_CREATED)
def create_model(payload: ModelCreate, db: Repository = Depends(repository)) -> dict[str, Any]:
    return create_or_conflict(lambda: db.create_model(payload.model_dump()))


@router.post("/models/{model_id}/activate")
def activate_model(model_id: int, db: Repository = Depends(repository)) -> dict[str, Any]:
    return require(db.activate_model(model_id))
