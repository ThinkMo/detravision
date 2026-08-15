from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.db import Database
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


def database(request: Request) -> Database:
    return request.app.state.db


def require(item: dict[str, Any] | None) -> dict[str, Any]:
    if item is None:
        raise HTTPException(status_code=404, detail="resource not found")
    return item


def create_or_conflict(db: Database, table: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        return db.create(table, data)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    runtime = request.app.state.runtime
    return {"status": "ok", "runtime": runtime.status()}


@router.get("/cameras")
def cameras(db: Database = Depends(database)) -> list[dict[str, Any]]:
    return db.list("camera")


@router.post("/cameras", status_code=status.HTTP_201_CREATED)
def create_camera(payload: CameraCreate, db: Database = Depends(database)) -> dict[str, Any]:
    return create_or_conflict(db, "camera", payload.model_dump())


@router.patch("/cameras/{camera_id}")
def update_camera(camera_id: int, payload: CameraUpdate, db: Database = Depends(database)) -> dict[str, Any]:
    return require(db.update("camera", camera_id, payload.model_dump(exclude_unset=True)))


@router.delete("/cameras/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(camera_id: int, db: Database = Depends(database)) -> None:
    if not db.delete("camera", camera_id):
        raise HTTPException(status_code=404, detail="camera not found")


@router.get("/persons")
def persons(db: Database = Depends(database)) -> list[dict[str, Any]]:
    return db.list("person")


@router.post("/persons", status_code=status.HTTP_201_CREATED)
def create_person(payload: PersonCreate, db: Database = Depends(database)) -> dict[str, Any]:
    return create_or_conflict(db, "person", payload.model_dump())


@router.patch("/persons/{person_id}")
def update_person(person_id: int, payload: PersonUpdate, db: Database = Depends(database)) -> dict[str, Any]:
    return require(db.update("person", person_id, payload.model_dump(exclude_unset=True)))


@router.delete("/persons/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_person(person_id: int, db: Database = Depends(database)) -> None:
    if not db.delete("person", person_id):
        raise HTTPException(status_code=404, detail="person not found")


@router.get("/alarm-groups")
def alarm_groups(db: Database = Depends(database)) -> list[dict[str, Any]]:
    groups = db.list("alarm_group")
    for group in groups:
        group["members"] = db.group_members(group["id"])
    return groups


@router.post("/alarm-groups", status_code=status.HTTP_201_CREATED)
def create_alarm_group(payload: AlarmGroupCreate, db: Database = Depends(database)) -> dict[str, Any]:
    group = create_or_conflict(db, "alarm_group", {"name": payload.name})
    try:
        db.replace_group_members(group["id"], payload.person_ids)
    except sqlite3.IntegrityError as exc:
        db.delete("alarm_group", group["id"])
        raise HTTPException(status_code=409, detail="person id does not exist") from exc
    group["members"] = db.group_members(group["id"])
    return group


@router.get("/events")
def events(
    camera_id: int | None = None,
    event_type: str | None = None,
    event_status: str | None = Query(default=None, alias="status"),
    db: Database = Depends(database),
) -> list[dict[str, Any]]:
    clauses, params = [], []
    for column, value in (("camera_id", camera_id), ("event_type", event_type), ("status", event_status)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    return db.list("detection_event", where=" AND ".join(clauses), params=params)


@router.patch("/events/{event_id}")
def update_event(event_id: int, payload: EventUpdate, db: Database = Depends(database)) -> dict[str, Any]:
    return require(db.update("detection_event", event_id, payload.model_dump()))


@router.get("/models")
def models(db: Database = Depends(database)) -> list[dict[str, Any]]:
    return db.list("model_version")


@router.post("/models", status_code=status.HTTP_201_CREATED)
def create_model(payload: ModelCreate, db: Database = Depends(database)) -> dict[str, Any]:
    return create_or_conflict(db, "model_version", payload.model_dump())


@router.post("/models/{model_id}/activate")
def activate_model(model_id: int, db: Database = Depends(database)) -> dict[str, Any]:
    return require(db.activate_model(model_id))
