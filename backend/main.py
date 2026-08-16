from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from backend.api.routes import router
from backend.core.config import settings
from backend.db import Repository
from backend.db.migrate import run_migrations
from backend.db.session import create_engine_from_url, create_session_factory
from backend.engine import build_detector
from backend.pipeline import RuntimeManager
from backend.ws import EventHub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations(settings.database_url)
    engine = create_engine_from_url(settings.database_url)
    repository = Repository(create_session_factory(engine))
    hub = EventHub()
    runtime = RuntimeManager(settings, repository, build_detector(settings.model_path), hub)
    app.state.repository = repository
    app.state.hub = hub
    app.state.runtime = runtime
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()
        engine.dispose()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.websocket("/ws/events")
async def event_stream(websocket: WebSocket) -> None:
    hub: EventHub = websocket.app.state.hub
    await hub.connect("events", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect("events", websocket)


@app.websocket("/ws/live/{camera_id}")
async def live_stream(websocket: WebSocket, camera_id: int) -> None:
    hub: EventHub = websocket.app.state.hub
    channel = f"live:{camera_id}"
    await hub.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(channel, websocket)
