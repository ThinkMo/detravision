from __future__ import annotations

import asyncio
import io
import json
import os
import zipfile
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import select
from starlette.responses import Response

from backend.api import batch_routes
from backend.audit import AuditService
from backend.auth import AuthService, hash_password
from backend.batch import BatchRepository, CompletedImage
from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import AuditLog, User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.engine import Detection, DetectorProvider
from backend.engine.detector import NoopDetector
from backend.main import create_app
from backend.storage import LocalFileStorage


MAX_IMAGE_BYTES = 10 * 1024 * 1024


class FakeDetector:
    def predict(self, _image) -> list[Detection]:
        return []


def image_bytes(
    image_format: str = "PNG",
    *,
    size: tuple[int, int] = (4, 3),
    mode: str = "RGB",
) -> bytes:
    output = io.BytesIO()
    Image.new(mode, size, 1 if mode == "1" else (19, 95, 87)).save(
        output,
        format=image_format,
    )
    return output.getvalue()


@pytest.fixture
def batch_app(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "batch-api.db").as_posix()
    upload_dir = tmp_path / "uploads"
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        users = [
            User(
                username="administrator",
                password_hash=hash_password("Admin-password-123"),
                role="admin",
                enabled=True,
                must_change_password=False,
            ),
            User(
                username="operator",
                password_hash=hash_password("Operator-password-123"),
                role="user",
                enabled=True,
                must_change_password=False,
            ),
            User(
                username="other",
                password_hash=hash_password("Other-password-123"),
                role="user",
                enabled=True,
                must_change_password=False,
            ),
        ]
        session.add_all(users)
        session.flush()
        user_ids = {user.username: user.id for user in users}

    settings = Settings(
        database_url=database_url,
        upload_dir=upload_dir,
        enable_runtime=False,
    )
    api_app = create_app(settings)
    auth_service = AuthService(session_factory, settings)
    storage = LocalFileStorage(upload_dir)
    repository = BatchRepository(session_factory)
    api_app.state.settings = settings
    api_app.state.session_factory = session_factory
    api_app.state.auth_service = auth_service
    api_app.state.audit_service = AuditService(session_factory)
    api_app.state.file_storage = storage
    api_app.state.batch_repository = repository
    api_app.state.detector_provider = DetectorProvider(FakeDetector(), None)
    api_app.state.cookie_secure = False

    try:
        yield api_app, auth_service, repository, storage, session_factory, user_ids
    finally:
        engine.dispose()


@contextmanager
def client_for(batch_app, role: str | None = None):
    api_app, auth_service, *_ = batch_app
    async_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://testserver",
    )
    if role is not None:
        credentials = {
            "admin": ("administrator", "Admin-password-123"),
            "user": ("operator", "Operator-password-123"),
            "other": ("other", "Other-password-123"),
        }
        username, password = credentials[role]
        async_client.cookies.set(
            "session",
            auth_service.login(username, password, "127.0.0.1").token,
        )

    class Client:
        def request(self, method: str, path: str, **kwargs) -> httpx.Response:
            return asyncio.run(async_client.request(method, path, **kwargs))

        def get(self, path: str, **kwargs) -> httpx.Response:
            return self.request("GET", path, **kwargs)

        def post(self, path: str, **kwargs) -> httpx.Response:
            return self.request("POST", path, **kwargs)

    try:
        yield Client()
    finally:
        asyncio.run(async_client.aclose())


def upload_files(*items: tuple[str, bytes, str]):
    return [("files", item) for item in items]


def stored_files(storage: LocalFileStorage) -> set[Path]:
    return {path for path in storage.root.rglob("*") if path.is_file()}


def asgi_scope(
    *,
    path: str = "/api/batch-jobs",
    method: str = "POST",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, object]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }


async def invoke_asgi(app, scope, receive):
    messages: list[dict[str, object]] = []

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


def response_from_asgi(messages) -> tuple[int, dict[str, object] | None]:
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), json.loads(body) if body else None


def test_batch_routes_require_authentication(batch_app):
    """Catches batch history or uploads becoming available without a session."""
    with client_for(batch_app) as client:
        assert client.get("/api/batch-jobs").status_code == 401
        assert client.post("/api/batch-jobs", files=[]).status_code == 401


def test_upload_rejects_zero_and_more_than_twenty_images(batch_app):
    """Catches unusable empty jobs or requests that exceed the fixed image count."""
    _, _, repository, storage, _, user_ids = batch_app
    png = image_bytes()
    with client_for(batch_app, "user") as client:
        empty = client.post("/api/batch-jobs", files=[])
        crowded = client.post(
            "/api/batch-jobs",
            files=upload_files(
                *((f"image-{index}.png", png, "image/png") for index in range(21))
            ),
        )

    assert empty.status_code == 422
    assert empty.json()["code"] == "no_images"
    assert crowded.status_code == 422
    assert crowded.json()["code"] == "too_many_images"
    assert repository.list_jobs(user_id=user_ids["operator"]).total == 0
    assert stored_files(storage) == set()


def test_upload_rejected_before_validation_without_loaded_model(batch_app):
    """Catches file reads or writes happening before model availability is checked."""
    api_app, _, repository, storage, _, user_ids = batch_app
    api_app.state.detector_provider.replace(NoopDetector(), None)
    with client_for(batch_app, "user") as client:
        response = client.post(
            "/api/batch-jobs",
            files=upload_files(("oversized.png", b"x" * (MAX_IMAGE_BYTES + 1), "image/png")),
        )

    assert response.status_code == 409
    assert response.json()["code"] == "model_unavailable"
    assert repository.list_jobs(user_id=user_ids["operator"]).total == 0
    assert stored_files(storage) == set()


def test_declared_oversized_batch_is_rejected_before_receive_parse_or_service(
    batch_app,
    monkeypatch,
):
    """Catches known oversized multipart bodies reaching temp spooling or the service."""
    api_app, *_ = batch_app
    max_bytes = getattr(batch_routes, "MAX_BATCH_REQUEST_BYTES", None)
    middleware_type = getattr(batch_routes, "BatchUploadLimitMiddleware", None)
    assert isinstance(max_bytes, int), "batch request byte limit is missing"
    assert middleware_type is not None, "batch upload limit middleware is missing"

    async def receive():
        raise AssertionError("oversized Content-Length must be rejected before receive")

    def service_must_not_run(*_args, **_kwargs):
        raise AssertionError("batch service must not run for an oversized body")

    def spool_must_not_run(*_args, **_kwargs):
        raise AssertionError("multipart temp spooling must not start")

    monkeypatch.setattr(batch_routes.BatchService, "create_job", service_must_not_run)
    monkeypatch.setattr(
        "starlette.formparsers.SpooledTemporaryFile",
        spool_must_not_run,
    )
    messages = asyncio.run(
        invoke_asgi(
            api_app,
            asgi_scope(
                headers=[
                    (b"content-type", b"multipart/form-data; boundary=limit-test"),
                    (b"content-length", str(max_bytes + 1).encode("ascii")),
                ]
            ),
            receive,
        )
    )
    status_code, payload = response_from_asgi(messages)
    assert status_code == 413
    assert payload == {
        "code": "batch_request_too_large",
        "message": "batch upload request exceeds the byte limit",
    }


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"content-length", b"1")],
        [(b"transfer-encoding", b"chunked")],
    ],
    ids=["missing-content-length", "forged-content-length", "chunked"],
)
def test_streamed_batch_limit_counts_receive_bytes_and_stops_early(headers):
    """Catches missing or untrusted length headers bypassing the receive-layer cap."""
    middleware_type = getattr(batch_routes, "BatchUploadLimitMiddleware", None)
    assert middleware_type is not None, "batch upload limit middleware is missing"
    receive_calls = 0
    chunks = [b"a" * 40, b"b" * 30, b"must-not-be-read"]

    async def receive():
        nonlocal receive_calls
        body = chunks[receive_calls]
        receive_calls += 1
        return {
            "type": "http.request",
            "body": body,
            "more_body": receive_calls < len(chunks),
        }

    async def consume_body(scope, downstream_receive, send):
        while True:
            message = await downstream_receive()
            if not message.get("more_body", False):
                break
        await Response(status_code=204)(scope, downstream_receive, send)

    middleware = middleware_type(consume_body, max_bytes=64)
    messages = asyncio.run(invoke_asgi(middleware, asgi_scope(headers=headers), receive))
    status_code, payload = response_from_asgi(messages)
    assert status_code == 413
    assert payload["code"] == "batch_request_too_large"
    assert receive_calls == 2


def test_batch_limit_allows_exact_boundary_and_does_not_limit_other_routes():
    """Catches an off-by-one cap or accidental application-wide request limit."""
    middleware_type = getattr(batch_routes, "BatchUploadLimitMiddleware", None)
    assert middleware_type is not None, "batch upload limit middleware is missing"

    async def consume_body(scope, receive, send):
        message = await receive()
        assert len(message["body"]) == 64
        await Response(status_code=204)(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b"x" * 64, "more_body": False}

    middleware = middleware_type(consume_body, max_bytes=64)
    exact = asyncio.run(invoke_asgi(middleware, asgi_scope(), receive))
    unrelated = asyncio.run(
        invoke_asgi(
            middleware,
            asgi_scope(path="/api/auth/login"),
            receive,
        )
    )
    assert response_from_asgi(exact)[0] == 204
    assert response_from_asgi(unrelated)[0] == 204


def test_upload_accepts_exactly_ten_mib_and_rejects_one_byte_more(batch_app):
    """Catches an off-by-one error in the per-image encoded byte cap."""
    png = image_bytes()
    exact = png + b"\0" * (MAX_IMAGE_BYTES - len(png))
    oversized = exact + b"x"
    with client_for(batch_app, "user") as client:
        accepted = client.post(
            "/api/batch-jobs",
            files=upload_files(("exact.png", exact, "image/png")),
        )
        rejected = client.post(
            "/api/batch-jobs",
            files=upload_files(("large.png", oversized, "image/png")),
        )

    assert accepted.status_code == 201
    assert rejected.status_code == 413
    assert rejected.json()["code"] == "image_too_large"


@pytest.mark.parametrize(
    ("filename", "content", "media_type", "status_code", "code"),
    [
        ("fake.jpg", image_bytes("PNG"), "image/jpeg", 415, "media_type_mismatch"),
        ("broken.png", b"not-an-image", "image/png", 400, "invalid_image"),
        ("notes.txt", b"plain text", "text/plain", 415, "unsupported_media_type"),
    ],
)
def test_upload_rejects_fake_mime_corrupt_and_unsupported_files(
    batch_app, filename, content, media_type, status_code, code
):
    """Catches trusting client MIME declarations instead of decoded image content."""
    _, _, repository, storage, _, user_ids = batch_app
    with client_for(batch_app, "user") as client:
        response = client.post(
            "/api/batch-jobs",
            files=upload_files((filename, content, media_type)),
        )
    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert repository.list_jobs(user_id=user_ids["operator"]).total == 0
    assert stored_files(storage) == set()


@pytest.mark.parametrize(
    ("filename", "image_format", "media_type"),
    [
        ("PHOTO.PNG", "PNG", "image/png"),
        ("photo.JpG", "JPEG", "image/jpeg"),
        ("photo.JpEg", "JPEG", "image/jpeg"),
        ("photo.WeBp", "WEBP", "image/webp"),
    ],
)
def test_upload_accepts_case_insensitive_matching_image_extensions(
    batch_app,
    filename,
    image_format,
    media_type,
):
    """Catches valid mixed-case allowed extensions being rejected."""
    with client_for(batch_app, "user") as client:
        response = client.post(
            "/api/batch-jobs",
            files=upload_files((filename, image_bytes(image_format), media_type)),
        )
    assert response.status_code == 201


@pytest.mark.parametrize(
    ("filename", "code"),
    [
        ("photo", "unsupported_file_extension"),
        ("photo.txt", "unsupported_file_extension"),
        ("photo.jpg", "media_type_mismatch"),
    ],
)
def test_upload_rejects_missing_unsupported_or_content_mismatched_extensions(
    batch_app,
    filename,
    code,
):
    """Catches absent or forged filename extensions bypassing format validation."""
    _, _, repository, storage, _, user_ids = batch_app
    with client_for(batch_app, "user") as client:
        response = client.post(
            "/api/batch-jobs",
            files=upload_files((filename, image_bytes("PNG"), "image/png")),
        )
    assert response.status_code == 415
    assert response.json()["code"] == code
    assert repository.list_jobs(user_id=user_ids["operator"]).total == 0
    assert stored_files(storage) == set()


def test_upload_rejects_more_than_forty_million_pixels(batch_app):
    """Catches compact decompression bombs bypassing the decoded-pixel cap."""
    content = image_bytes(size=(10_000, 4_001), mode="1")
    assert len(content) < MAX_IMAGE_BYTES
    with client_for(batch_app, "user") as client:
        response = client.post(
            "/api/batch-jobs",
            files=upload_files(("wide.png", content, "image/png")),
        )
    assert response.status_code == 413
    assert response.json()["code"] == "pixel_limit_exceeded"


def test_failed_multi_file_upload_removes_saved_files_and_database_rows(batch_app):
    """Catches a later invalid image leaving earlier files or a partial job behind."""
    _, _, repository, storage, _, user_ids = batch_app
    with client_for(batch_app, "user") as client:
        response = client.post(
            "/api/batch-jobs",
            files=upload_files(
                ("valid.png", image_bytes(), "image/png"),
                ("broken.png", b"broken", "image/png"),
            ),
        )
    assert response.status_code == 400
    assert repository.list_jobs(user_id=user_ids["operator"]).total == 0
    assert stored_files(storage) == set()


def test_database_failure_rolls_back_every_saved_source(batch_app, monkeypatch):
    """Catches a failed job transaction orphaning request files in local storage."""
    _, _, repository, storage, _, _ = batch_app

    def fail_create(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(repository, "create_job", fail_create)
    with client_for(batch_app, "user") as client:
        response = client.post(
            "/api/batch-jobs",
            files=upload_files(("valid.png", image_bytes(), "image/png")),
        )
    assert response.status_code == 503
    assert response.json()["code"] == "batch_create_failed"
    assert stored_files(storage) == set()


def test_ordinary_users_see_only_their_jobs_while_admin_can_filter(batch_app):
    """Catches list/detail ownership leaks or an admin user filter being ignored."""
    _, _, _, _, _, user_ids = batch_app
    with client_for(batch_app, "user") as user_client:
        own = user_client.post(
            "/api/batch-jobs",
            files=upload_files(("own.png", image_bytes(), "image/png")),
        ).json()
    with client_for(batch_app, "other") as other_client:
        private = other_client.post(
            "/api/batch-jobs",
            files=upload_files(("private.png", image_bytes(), "image/png")),
        ).json()
        hidden = other_client.get(f"/api/batch-jobs/{own['id']}")
        own_list = other_client.get("/api/batch-jobs?page=1&page_size=100")
    with client_for(batch_app, "admin") as admin_client:
        filtered = admin_client.get(
            f"/api/batch-jobs?user_id={user_ids['operator']}&page=1&page_size=100"
        )

    assert hidden.status_code == 404
    assert [item["id"] for item in own_list.json()["items"]] == [private["id"]]
    assert [item["id"] for item in filtered.json()["items"]] == [own["id"]]


def test_list_filters_status_and_inclusive_offset_timestamps(batch_app):
    """Catches list filters being accepted by the route but ignored by persistence."""
    with client_for(batch_app, "user") as client:
        job = client.post(
            "/api/batch-jobs",
            files=upload_files(("one.png", image_bytes(), "image/png")),
        ).json()
        matching = client.get(
            "/api/batch-jobs",
            params={
                "status": "queued",
                "created_from": job["created_at"],
                "created_to": job["created_at"],
            },
        )
        excluded = client.get("/api/batch-jobs", params={"status": "completed"})

    assert [item["id"] for item in matching.json()["items"]] == [job["id"]]
    assert excluded.json()["items"] == []


def test_list_rejects_naive_time_filters_without_database_error(batch_app):
    """Catches a timezone-less filter escaping as an unstable database-backed 500."""
    with client_for(batch_app, "user") as client:
        response = client.get(
            "/api/batch-jobs",
            params={"created_from": "2030-01-02T03:04:05"},
        )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_time_filter"


def test_detail_has_safe_result_urls_and_no_storage_keys(batch_app):
    """Catches internal source/result keys being serialized in the public detail DTO."""
    with client_for(batch_app, "user") as client:
        job = client.post(
            "/api/batch-jobs",
            files=upload_files(("../../unsafe.png", image_bytes(), "image/png")),
        ).json()
        detail = client.get(f"/api/batch-jobs/{job['id']}")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["images"][0]["display_name"] == "unsafe.png"
    assert payload["images"][0]["result_url"].endswith(
        f"/images/{payload['images'][0]['id']}/download"
    )
    assert "source_key" not in detail.text
    assert "annotated_key" not in detail.text
    assert "batch-source" not in detail.text


def _complete_first_image(batch_app, job_id: int, content: bytes = b"annotated"):
    _, _, repository, storage, _, _ = batch_app
    claimed = repository.claim_next_image()
    assert claimed is not None and claimed.job_id == job_id
    key = storage.save(io.BytesIO(content), "batch-result", "jpg")
    repository.complete_image(
        claimed.id,
        CompletedImage(
            annotated_key=key,
            model_version_id=None,
            detections=[{"label": "helmet", "confidence": 0.9, "bbox": [1, 2, 3, 4]}],
        ),
    )
    return claimed.id


def test_single_download_serves_only_completed_annotation_with_safe_filename(batch_app):
    """Catches source download exposure, pre-result downloads, or header injection."""
    source = image_bytes()
    with client_for(batch_app, "user") as client:
        job = client.post(
            "/api/batch-jobs",
            files=upload_files(("../../report\r\nX-Evil: yes.png", source, "image/png")),
        ).json()
        detail = client.get(f"/api/batch-jobs/{job['id']}").json()
        image_id = detail["images"][0]["id"]
        unavailable = client.get(
            f"/api/batch-jobs/{job['id']}/images/{image_id}/download"
        )
        _complete_first_image(batch_app, job["id"], b"annotated-only")
        downloaded = client.get(
            f"/api/batch-jobs/{job['id']}/images/{image_id}/download"
        )

    assert unavailable.status_code == 404
    assert downloaded.status_code == 200
    assert downloaded.content == b"annotated-only"
    assert downloaded.content != source
    disposition = downloaded.headers["content-disposition"]
    assert "attachment" in disposition
    assert ".." not in disposition and "\r" not in disposition and "\n" not in disposition


def test_job_zip_contains_manifest_and_successful_annotations_only(batch_app):
    """Catches ZIP exports leaking source files or opaque storage keys."""
    with client_for(batch_app, "user") as client:
        job = client.post(
            "/api/batch-jobs",
            files=upload_files(
                ("first.png", image_bytes(), "image/png"),
                ("second.png", image_bytes(), "image/png"),
            ),
        ).json()
        _complete_first_image(batch_app, job["id"], b"first-annotation")
        second = batch_app[2].claim_next_image()
        assert second is not None
        batch_app[2].fail_image(second.id, "inference_failed", "inference failed")
        response = client.get(f"/api/batch-jobs/{job['id']}/download")

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert names == ["results.json", "001-first.jpg"]
        assert archive.read("001-first.jpg") == b"first-annotation"
        manifest = json.loads(archive.read("results.json"))
    serialized = json.dumps(manifest)
    assert manifest["id"] == job["id"]
    assert [item["status"] for item in manifest["images"]] == ["completed", "failed"]
    assert "source_key" not in serialized and "annotated_key" not in serialized
    assert "batch-source" not in serialized and "batch-result" not in serialized


def test_zip_temporary_file_is_deleted_after_response(batch_app, monkeypatch, tmp_path):
    """Catches completed ZIP responses accumulating server-side temporary files."""
    created = tmp_path / "observable-export.zip"

    def controlled_mkstemp(*_args, **_kwargs):
        descriptor = os.open(
            created,
            os.O_CREAT | os.O_EXCL | os.O_RDWR,
        )
        return descriptor, str(created)

    monkeypatch.setattr("backend.batch.service.tempfile.mkstemp", controlled_mkstemp)
    with client_for(batch_app, "user") as client:
        job = client.post(
            "/api/batch-jobs",
            files=upload_files(("one.png", image_bytes(), "image/png")),
        ).json()
        _complete_first_image(batch_app, job["id"])
        response = client.get(f"/api/batch-jobs/{job['id']}/download")
    assert response.status_code == 200
    assert not created.exists()


def test_upload_is_audited_without_filenames_or_storage_paths(batch_app):
    """Catches batch mutations omitting audit records or recording local file details."""
    _, _, _, storage, session_factory, _ = batch_app
    with client_for(batch_app, "user") as client:
        response = client.post(
            "/api/batch-jobs",
            files=upload_files(("private-name.png", image_bytes(), "image/png")),
        )
    assert response.status_code == 201
    with session_factory() as session:
        record = session.scalar(
            select(AuditLog)
            .where(AuditLog.action == "batch_job_create")
            .order_by(AuditLog.id.desc())
        )
    assert record is not None and record.outcome == "success"
    serialized = (record.details or "") + response.text
    assert "private-name" not in (record.details or "")
    assert str(storage.root) not in serialized
    assert "batch-source" not in serialized
