from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from backend.auth.dependencies import current_user, require_same_origin
from backend.db import Repository


router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_same_origin), Depends(current_user)],
)

_IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@router.get("/events/{event_id}/snapshot", response_class=FileResponse)
def event_snapshot(event_id: int, request: Request) -> FileResponse:
    repository: Repository = request.app.state.repository
    event = repository.get_event(event_id)
    if event is None or not event["snapshot_path"]:
        raise HTTPException(status_code=404, detail="snapshot not found")

    root = Path(request.app.state.settings.snapshot_dir).resolve()
    stored = Path(event["snapshot_path"])
    candidates = [stored.resolve()] if stored.is_absolute() else [
        (root / stored).resolve(),
        stored.resolve(),
    ]
    candidate = next(
        (path for path in candidates if path.is_relative_to(root) and path.is_file()),
        None,
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    media_type = _IMAGE_MEDIA_TYPES.get(candidate.suffix.casefold())
    if media_type is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return FileResponse(candidate, media_type=media_type)
