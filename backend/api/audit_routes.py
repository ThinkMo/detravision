from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
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
) -> list[dict[str, object]]:
    with request.app.state.session_factory() as session:
        rows = session.scalars(
            select(AuditLog)
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
