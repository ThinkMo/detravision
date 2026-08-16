from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from backend.api.audit_routes import router as audit_router
from backend.api.auth_routes import router as auth_router
from backend.api.dashboard_routes import router as dashboard_router
from backend.api.media_routes import router as media_router
from backend.api.routes import router
from backend.api.site_routes import router as site_router
from backend.api.user_routes import router as user_router
from backend.audit import AuditService
from backend.auth import AuthenticatedUser, AuthService
from backend.auth.dependencies import SESSION_COOKIE_NAME
from backend.brand import BrandService
from backend.core.config import Settings, settings
from backend.db import Repository
from backend.db.migrate import run_migrations
from backend.db.session import create_engine_from_url, create_session_factory
from backend.engine import DetectorProvider, build_detector
from backend.pipeline import RuntimeManager
from backend.storage import LocalFileStorage
from backend.web import mount_frontend
from backend.ws import EventHub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

FRONTEND_DIST_DIR = Path(__file__).resolve().parents[1] / "frontend" / "dist"


def application_lifespan(app_settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app_settings.environment.casefold() == "development":
            run_migrations(app_settings.database_url)
        engine = create_engine_from_url(app_settings.database_url)
        session_factory = create_session_factory(engine)
        repository = Repository(session_factory)
        auth_service = AuthService(session_factory, app_settings)
        audit_service = AuditService(session_factory)
        file_storage = LocalFileStorage(app_settings.upload_dir)
        brand_service = BrandService(session_factory, file_storage)
        auth_service.bootstrap_admin()
        brand_service.cleanup_pending_logos()
        hub = EventHub()
        detector_provider = DetectorProvider(
            build_detector(app_settings.model_path),
            model_version_id=None,
        )
        runtime = RuntimeManager(
            app_settings,
            repository,
            detector_provider,
            hub,
        )
        app.state.settings = app_settings
        app.state.repository = repository
        app.state.session_factory = session_factory
        app.state.auth_service = auth_service
        app.state.audit_service = audit_service
        app.state.file_storage = file_storage
        app.state.brand_service = brand_service
        app.state.cookie_secure = app_settings.cookie_secure
        app.state.hub = hub
        app.state.detector_provider = detector_provider
        app.state.runtime = runtime
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()
            engine.dispose()

    return lifespan


async def authenticate_websocket(
    websocket: WebSocket,
) -> AuthenticatedUser | None:
    raw_token = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        await websocket.close(code=4401)
        return None
    user = websocket.app.state.auth_service.authenticate(raw_token)
    if user is None:
        await websocket.close(code=4401)
        return None
    if user.must_change_password:
        await websocket.close(code=4403)
        return None
    return user


async def event_stream(websocket: WebSocket) -> None:
    if await authenticate_websocket(websocket) is None:
        return
    hub: EventHub = websocket.app.state.hub
    await hub.connect("events", websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect("events", websocket)


async def live_stream(websocket: WebSocket, camera_id: int) -> None:
    if await authenticate_websocket(websocket) is None:
        return
    hub: EventHub = websocket.app.state.hub
    channel = f"live:{camera_id}"
    await hub.connect(channel, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(channel, websocket)


def create_app(app_settings: Settings | None = None) -> FastAPI:
    configured = app_settings or settings
    application = FastAPI(
        title=configured.app_name,
        version="0.1.0",
        lifespan=application_lifespan(configured),
    )
    application.include_router(router)
    application.include_router(auth_router)
    application.include_router(user_router)
    application.include_router(audit_router)
    application.include_router(site_router)
    application.include_router(dashboard_router)
    application.include_router(media_router)
    application.add_api_websocket_route("/ws/events", event_stream)
    application.add_api_websocket_route("/ws/live/{camera_id}", live_stream)
    mount_frontend(application, FRONTEND_DIST_DIR)
    return application


app = create_app()
