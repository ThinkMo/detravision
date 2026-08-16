from __future__ import annotations

import io
import asyncio
import time
from datetime import datetime, timezone

import cv2
import numpy as np
import pytest

from backend.batch import BatchRepository, PendingImage, annotate
from backend.batch.worker import BatchWorker
from backend import main as main_module
from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import ModelVersion, User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.engine import Detection, DetectorProvider
from backend.storage import LocalFileStorage


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FakeDetector:
    def predict(self, image: np.ndarray) -> list[Detection]:
        height, width = image.shape[:2]
        return [Detection("helmet", 0.916, (-2, -1, width + 3, height + 4))]


@pytest.fixture
def batch_context(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "batch-worker.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        owner = User(
            username="owner",
            password_hash="unused",
            role="user",
            enabled=True,
            must_change_password=False,
        )
        session.add(owner)
        session.flush()
        session.add(
            ModelVersion(
                id=8,
                name="test-model",
                file_path="opaque/model/key",
                runtime="onnx-fp32",
                classes='["helmet"]',
                is_active=True,
            )
        )
        owner_id = owner.id
    repository = BatchRepository(session_factory, clock=lambda: NOW)
    storage = LocalFileStorage(tmp_path / "uploads")
    provider = DetectorProvider(FakeDetector(), model_version_id=8)
    worker = BatchWorker(repository, provider, storage, poll_interval=0.01)
    try:
        yield repository, storage, worker, owner_id
    finally:
        worker.stop()
        engine.dispose()


def _jpeg_bytes() -> bytes:
    image = np.full((40, 60, 3), 240, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def _create_job(repository, storage, owner_id, payloads):
    pending = []
    for index, payload in enumerate(payloads):
        key = storage.save(io.BytesIO(payload), "batch-source", "jpg")
        pending.append(PendingImage(f"image-{index}.jpg", key))
    return repository.create_job(owner_id, pending)


def test_worker_continues_after_one_image_fails(batch_context):
    repository, storage, worker, owner_id = batch_context
    job = _create_job(repository, storage, owner_id, [_jpeg_bytes(), b"corrupt"])

    assert worker.process_available() == 2

    result = repository.get_job(job.id)
    assert result is not None
    assert (result.status, result.success_count, result.failure_count) == (
        "partial_failed",
        1,
        1,
    )
    images = repository.list_images(job.id)
    assert images[1].error_code == "decode_failed"
    assert images[1].error_message == "image could not be decoded"


def test_worker_records_model_version_detections_and_jpeg_annotation(batch_context):
    repository, storage, worker, owner_id = batch_context
    job = _create_job(repository, storage, owner_id, [_jpeg_bytes()])

    worker.process_available()

    image = repository.list_images(job.id)[0]
    assert image.model_version_id == 8
    assert image.detections == [
        {"label": "helmet", "confidence": 0.916, "bbox": [-2, -1, 63, 44]}
    ]
    assert image.annotated_key is not None
    with storage.open(image.annotated_key) as stream:
        encoded = np.frombuffer(stream.read(), dtype=np.uint8)
    annotated = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    assert annotated is not None
    assert annotated.shape == (40, 60, 3)
    assert not np.all(annotated == 240)


def test_worker_start_recovers_interrupted_work_and_stop_is_prompt(batch_context):
    repository, storage, worker, owner_id = batch_context
    job = _create_job(repository, storage, owner_id, [_jpeg_bytes()])
    assert repository.claim_next_image() is not None

    worker.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if repository.get_job(job.id).status == "completed":
            break
        time.sleep(0.01)
    started = time.monotonic()
    worker.stop()

    assert repository.get_job(job.id).status == "completed"
    assert time.monotonic() - started < 0.5


def test_annotation_is_deterministic_and_clamps_out_of_bounds_boxes():
    image = np.full((20, 30, 3), 240, dtype=np.uint8)
    detections = [Detection("helmet", 0.916, (-100, -100, 1000, 1000))]

    first = annotate(image, detections)
    second = annotate(image, detections)

    assert np.array_equal(first, second)
    assert first.shape == image.shape
    assert not np.array_equal(first, image)


def test_app_startup_recovery_uses_database_active_model_before_batch_claim(
    tmp_path,
    monkeypatch,
):
    database_url = "sqlite:///" + (tmp_path / "startup.db").as_posix()
    upload_dir = tmp_path / "uploads"
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        owner = User(
            username="owner",
            password_hash="unused",
            role="user",
            enabled=True,
            must_change_password=False,
        )
        model = ModelVersion(
            name="database-active",
            file_path="opaque/active-model.onnx",
            runtime="onnx-fp32",
            classes='["helmet"]',
            is_active=True,
        )
        session.add_all([owner, model])
        session.flush()
        owner_id, model_id = owner.id, model.id
    repository = BatchRepository(session_factory, clock=lambda: NOW)
    storage = LocalFileStorage(upload_dir)
    source_key = storage.save(io.BytesIO(_jpeg_bytes()), "batch-source", "jpg")
    job = repository.create_job(owner_id, [PendingImage("recover.jpg", source_key)])
    assert repository.claim_next_image() is not None
    engine.dispose()

    monkeypatch.setattr(
        "backend.pipeline.runtime.build_detector",
        lambda _path: FakeDetector(),
    )
    app = main_module.create_app(
        Settings(
            database_url=database_url,
            upload_dir=upload_dir,
            model_path=None,
            enable_runtime=False,
        )
    )

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                current = app.state.batch_repository.get_job(job.id)
                if current is not None and current.status == "completed":
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("recovered batch image was not completed")

    asyncio.run(exercise())

    image = repository.list_images(job.id)[0]
    assert image.status == "completed"
    assert image.model_version_id == model_id
