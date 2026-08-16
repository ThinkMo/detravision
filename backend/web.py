from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


_RESERVED_PREFIXES = ("api", "ws", "assets")


def mount_frontend(app: FastAPI, dist_dir: Path) -> None:
    """Serve a Vite build without intercepting backend namespaces."""

    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa(spa_path: str) -> FileResponse:
        first_segment = spa_path.partition("/")[0]
        if first_segment in _RESERVED_PREFIXES:
            raise HTTPException(status_code=404)

        index = dist_dir / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=503,
                detail="frontend build is unavailable",
            )
        return FileResponse(index)
