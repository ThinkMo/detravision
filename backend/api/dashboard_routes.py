from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from backend.auth.dependencies import current_user, require_same_origin
from backend.db import Repository


class DashboardEvent(BaseModel):
    id: int
    camera_id: int
    event_type: str
    confidence: float
    bbox: list[float]
    snapshot_path: str | None
    status: Literal["new", "ack", "closed"]
    ack_by: int | None
    ack_user_id: int | None
    created_at: str


class DashboardSummary(BaseModel):
    camera_total: int
    camera_active: int
    events_today: int
    events_new: int
    recent_events: list[DashboardEvent]
    runtime: dict[str, Any]


router = APIRouter(
    prefix="/api/dashboard",
    dependencies=[Depends(require_same_origin), Depends(current_user)],
)


@router.get("/summary", response_model=DashboardSummary)
def dashboard_summary(request: Request) -> DashboardSummary:
    repository: Repository = request.app.state.repository
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    camera_total, camera_active = repository.dashboard_camera_counts()
    events_today, events_new = repository.dashboard_event_counts(day_start)
    return DashboardSummary(
        camera_total=camera_total,
        camera_active=camera_active,
        events_today=events_today,
        events_new=events_new,
        recent_events=repository.list_recent_events(limit=8),
        runtime=request.app.state.runtime.status(),
    )
