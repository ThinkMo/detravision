from __future__ import annotations

from typing import Any


def public_camera(record: dict[str, Any]) -> dict[str, Any]:
    """Return a camera DTO without the persisted stream credential."""
    return {
        key: value
        for key, value in record.items()
        if key != "rtsp_url"
    } | {"rtsp_configured": bool(record.get("rtsp_url"))}


def public_model(record: dict[str, Any]) -> dict[str, Any]:
    """Return a model DTO without its server-local storage path."""
    return {key: value for key, value in record.items() if key != "file_path"}


def public_event(record: dict[str, Any]) -> dict[str, Any]:
    """Return an event DTO that references only the authenticated media endpoint."""
    snapshot_url = (
        f"/api/events/{record['id']}/snapshot"
        if record.get("snapshot_path")
        else None
    )
    return {
        key: value
        for key, value in record.items()
        if key != "snapshot_path"
    } | {"snapshot_url": snapshot_url}
