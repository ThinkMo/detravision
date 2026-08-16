from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from backend.api import routes
from backend.auth import AuthenticatedUser
from backend.auth.dependencies import current_user
from backend.audit import AuditService
from backend.db import Repository
from backend.db.migrate import run_migrations
from backend.db.models import User
from backend.db.session import create_engine_from_url, create_session_factory


@pytest.fixture
def group_app(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "groups.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        session.add(
            User(
                username="groups-admin",
                password_hash="unused",
                role="admin",
                enabled=True,
                must_change_password=False,
            )
        )
    app = FastAPI()
    app.include_router(routes.router)
    app.state.repository = Repository(session_factory)
    app.state.session_factory = session_factory
    app.state.audit_service = AuditService(session_factory)
    app.dependency_overrides[current_user] = lambda: AuthenticatedUser(
        id=1,
        username="groups-admin",
        role="admin",
        must_change_password=False,
    )
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def request(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def seed_person(repository: Repository, name: str) -> dict[str, object]:
    return repository.create_person(
        {"name": name, "phone": None, "feishu_id": None, "dept": None}
    )


def test_admin_updates_group_name_and_members_in_one_request(group_app):
    repository = group_app.state.repository
    old_member = seed_person(repository, "旧联系人")
    new_member = seed_person(repository, "夜班联系人")
    group = repository.create_alarm_group({"name": "旧告警组"})
    repository.replace_group_members(group["id"], [old_member["id"]])

    response = request(
        group_app,
        "PATCH",
        f"/api/alarm-groups/{group['id']}",
        json={"name": "夜班告警", "person_ids": [new_member["id"]]},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "夜班告警"
    assert [member["id"] for member in response.json()["members"]] == [new_member["id"]]


def test_missing_member_rejects_update_without_partial_changes(group_app):
    repository = group_app.state.repository
    member = seed_person(repository, "值班联系人")
    group = repository.create_alarm_group({"name": "原告警组"})
    repository.replace_group_members(group["id"], [member["id"]])

    response = request(
        group_app,
        "PATCH",
        f"/api/alarm-groups/{group['id']}",
        json={"name": "不应保存", "person_ids": [99999]},
    )

    assert response.status_code == 409
    listed = request(group_app, "GET", "/api/alarm-groups").json()
    assert listed[0]["name"] == "原告警组"
    assert [item["id"] for item in listed[0]["members"]] == [member["id"]]


def test_group_used_by_camera_returns_stable_conflict(group_app):
    repository = group_app.state.repository
    group = repository.create_alarm_group({"name": "生产告警组"})
    repository.create_camera(
        {
            "name": "一号门",
            "rtsp_url": "rtsp://example.invalid/live",
            "enabled": True,
            "sample_fps": 1,
            "roi": [],
            "detect_classes": ["no_helmet"],
            "alarm_group_id": group["id"],
        }
    )

    response = request(group_app, "DELETE", f"/api/alarm-groups/{group['id']}")

    assert response.status_code == 409
    assert response.json()["detail"] == "resource_in_use"


def test_missing_group_update_and_delete_return_not_found(group_app):
    updated = request(
        group_app,
        "PATCH",
        "/api/alarm-groups/99999",
        json={"name": "不存在", "person_ids": []},
    )
    deleted = request(group_app, "DELETE", "/api/alarm-groups/99999")

    assert updated.status_code == 404
    assert deleted.status_code == 404
