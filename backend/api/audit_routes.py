from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from backend.auth.dependencies import require_admin
from backend.db.models import AuditLog


router = APIRouter(
    prefix="/api/audit-logs",
    tags=["audit"],
    dependencies=[Depends(require_admin)],
)


@router.get("")
def list_audit_logs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor_id: int | None = Query(default=None, ge=1),
    action: str | None = Query(default=None, min_length=1, max_length=100),
    outcome: str | None = Query(default=None, min_length=1, max_length=40),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> list[dict[str, object]]:
    for value in (created_from, created_to):
        if value is not None and value.tzinfo is None:
            raise HTTPException(
                status_code=422,
                detail="audit time filters must include a UTC offset",
            )
    statement = select(AuditLog)
    if actor_id is not None:
        statement = statement.where(AuditLog.actor_id == actor_id)
    if action is not None:
        statement = statement.where(AuditLog.action == action)
    if outcome is not None:
        statement = statement.where(AuditLog.outcome == outcome)
    if created_from is not None:
        statement = statement.where(AuditLog.created_at >= created_from.astimezone(timezone.utc))
    if created_to is not None:
        statement = statement.where(AuditLog.created_at <= created_to.astimezone(timezone.utc))
    with request.app.state.session_factory() as session:
        rows = session.scalars(
            statement
            .order_by(AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            {
                "id": row.id,
                "actor_id": row.actor_id,
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "outcome": row.outcome,
                "details": row.details,
                "ip": row.ip,
                "created_at": row.created_at,
            }
            for row in rows
        ]
