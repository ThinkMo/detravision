from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from .models import AlarmGroup, Camera, DetectionEvent, ModelVersion, Person, alarm_group_member


def utc_iso(value: datetime) -> str:
    return value.isoformat()


def camera_dict(row: Camera) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "rtsp_url": row.rtsp_url,
        "enabled": row.enabled,
        "sample_fps": row.sample_fps,
        "roi": json.loads(row.roi),
        "detect_classes": json.loads(row.detect_classes),
        "alarm_group_id": row.alarm_group_id,
        "created_at": utc_iso(row.created_at),
    }


def person_dict(row: Person) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "phone": row.phone,
        "feishu_id": row.feishu_id,
        "dept": row.dept,
        "created_at": utc_iso(row.created_at),
    }


def alarm_group_dict(row: AlarmGroup) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "created_at": utc_iso(row.created_at)}


def event_dict(row: DetectionEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "camera_id": row.camera_id,
        "event_type": row.event_type,
        "confidence": row.confidence,
        "bbox": json.loads(row.bbox),
        "snapshot_path": row.snapshot_path,
        "status": row.status,
        "ack_by": row.ack_by,
        "ack_user_id": row.ack_user_id,
        "created_at": utc_iso(row.created_at),
    }


def model_dict(row: ModelVersion) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "file_path": row.file_path,
        "runtime": row.runtime,
        "classes": json.loads(row.classes),
        "map50": row.map50,
        "is_active": row.is_active,
        "created_at": utc_iso(row.created_at),
    }


class Repository:
    """Explicit persistence operations that return JSON-ready API records."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def _list(self, statement, convert: Callable[[Any], dict[str, Any]]) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            return [convert(row) for row in session.scalars(statement)]

    def list_cameras(self) -> list[dict[str, Any]]:
        return self._list(select(Camera).order_by(Camera.id.desc()), camera_dict)

    def list_enabled_cameras(self) -> list[dict[str, Any]]:
        return self._list(select(Camera).where(Camera.enabled.is_(True)).order_by(Camera.id.desc()), camera_dict)

    def dashboard_camera_counts(self) -> tuple[int, int]:
        """Return total and enabled camera counts in one portable aggregate."""
        statement = select(
            func.count(Camera.id),
            func.coalesce(func.sum(case((Camera.enabled.is_(True), 1), else_=0)), 0),
        )
        with self.session_factory() as session:
            total, active = session.execute(statement).one()
            return int(total), int(active)

    def get_camera(self, camera_id: int) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(Camera, camera_id)
            return camera_dict(row) if row else None

    def create_camera(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._create(
            Camera,
            {**data, "roi": json.dumps(data["roi"], ensure_ascii=False), "detect_classes": json.dumps(data["detect_classes"], ensure_ascii=False)},
            camera_dict,
        )

    def update_camera(self, camera_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        return self._update(
            Camera,
            camera_id,
            self._json_fields(data, {"roi", "detect_classes"}),
            camera_dict,
        )

    def delete_camera(self, camera_id: int) -> bool:
        return self._delete(Camera, camera_id)

    def list_persons(self) -> list[dict[str, Any]]:
        return self._list(select(Person).order_by(Person.id.desc()), person_dict)

    def create_person(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._create(Person, data, person_dict)

    def update_person(self, person_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        return self._update(Person, person_id, data, person_dict)

    def delete_person(self, person_id: int) -> bool:
        return self._delete(Person, person_id)

    def list_alarm_groups(self) -> list[dict[str, Any]]:
        return self._list(select(AlarmGroup).order_by(AlarmGroup.id.desc()), alarm_group_dict)

    def create_alarm_group(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._create(AlarmGroup, data, alarm_group_dict)

    def delete_alarm_group(self, group_id: int) -> bool:
        return self._delete(AlarmGroup, group_id)

    def replace_group_members(self, group_id: int, person_ids: list[int]) -> None:
        with self.session_factory.begin() as session:
            session.execute(alarm_group_member.delete().where(alarm_group_member.c.group_id == group_id))
            members = [{"group_id": group_id, "person_id": person_id} for person_id in person_ids]
            if members:
                session.execute(alarm_group_member.insert(), members)

    def group_members(self, group_id: int) -> list[dict[str, Any]]:
        statement = (
            select(Person)
            .join(alarm_group_member, Person.id == alarm_group_member.c.person_id)
            .where(alarm_group_member.c.group_id == group_id)
        )
        return self._list(statement, person_dict)

    def list_events(
        self, *, camera_id: int | None = None, event_type: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        statement = select(DetectionEvent).order_by(DetectionEvent.id.desc())
        if camera_id is not None:
            statement = statement.where(DetectionEvent.camera_id == camera_id)
        if event_type is not None:
            statement = statement.where(DetectionEvent.event_type == event_type)
        if status is not None:
            statement = statement.where(DetectionEvent.status == status)
        return self._list(statement, event_dict)

    def dashboard_event_counts(self, day_start: datetime) -> tuple[int, int]:
        """Return today's total and currently new events without DB date functions."""
        statement = select(
            func.count(DetectionEvent.id),
            func.coalesce(
                func.sum(case((DetectionEvent.status == "new", 1), else_=0)),
                0,
            ),
        ).where(DetectionEvent.created_at >= day_start)
        with self.session_factory() as session:
            total, new = session.execute(statement).one()
            return int(total), int(new)

    def list_recent_events(self, limit: int = 8) -> list[dict[str, Any]]:
        statement = (
            select(DetectionEvent)
            .order_by(DetectionEvent.created_at.desc(), DetectionEvent.id.desc())
            .limit(limit)
        )
        return self._list(statement, event_dict)

    def create_event(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._create(DetectionEvent, self._json_fields(data, {"bbox"}), event_dict)

    def update_event(self, event_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        return self._update(DetectionEvent, event_id, self._json_fields(data, {"bbox"}), event_dict)

    def list_models(self) -> list[dict[str, Any]]:
        return self._list(select(ModelVersion).order_by(ModelVersion.id.desc()), model_dict)

    def get_active_model(self) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.scalar(select(ModelVersion).where(ModelVersion.is_active.is_(True)).order_by(ModelVersion.id.desc()))
            return model_dict(row) if row else None

    def create_model(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._create(ModelVersion, self._json_fields(data, {"classes"}), model_dict)

    def activate_model(self, model_id: int) -> dict[str, Any] | None:
        with self.session_factory.begin() as session:
            row = session.get(ModelVersion, model_id)
            if row is None:
                return None
            session.execute(update(ModelVersion).values(is_active=False))
            row.is_active = True
            session.flush()
            session.refresh(row)
            return model_dict(row)

    def _create(self, model, data: dict[str, Any], convert: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            row = model(**data)
            session.add(row)
            session.flush()
            session.refresh(row)
            return convert(row)

    def _update(self, model, item_id: int, data: dict[str, Any], convert: Callable[[Any], dict[str, Any]]) -> dict[str, Any] | None:
        with self.session_factory.begin() as session:
            row = session.get(model, item_id)
            if row is None:
                return None
            for key, value in data.items():
                setattr(row, key, value)
            session.flush()
            session.refresh(row)
            return convert(row)

    def _delete(self, model, item_id: int) -> bool:
        with self.session_factory.begin() as session:
            row = session.get(model, item_id)
            if row is None:
                return False
            session.delete(row)
            return True

    @staticmethod
    def _json_fields(data: dict[str, Any], fields: set[str]) -> dict[str, Any]:
        return {key: json.dumps(value, ensure_ascii=False) if key in fields else value for key, value in data.items()}
