from __future__ import annotations

import io
import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import pytest

from backend.batch import BatchRepository, PendingImage, RetentionCleaner, annotate
from backend.batch.annotate import annotation_label
from backend.batch.worker import BatchWorker
from backend import main as main_module
from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import ModelVersion, User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.engine import Detection, DetectorProvider, NoopDetector
from backend.pipeline import RuntimeManager
from backend.pipeline import runtime as runtime_module
from backend.ws import EventHub
from backend.storage import LocalFileStorage


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FakeDetector:
    def predict(self, image: np.ndarray) -> list[Detection]:
        height, width = image.shape[:2]
        return [
            Detection("person", 0.88, (0, 0, width - 1, height - 1)),
            Detection("helmet", 0.916, (20, 0, 40, 15)),
        ]


class PersonOnlyDetector:
    def predict(self, image: np.ndarray) -> list[Detection]:
        height, width = image.shape[:2]
        return [Detection("person", 0.87, (0, 0, width - 1, height - 1))]


class BlockingFakeDetector(FakeDetector):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def predict(self, image: np.ndarray) -> list[Detection]:
        self.started.set()
        assert self.release.wait(timeout=2)
        return super().predict(image)


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
        {"label": "helmet_worn", "confidence": 0.916, "bbox": [0, 0, 59, 39]}
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


def test_annotation_prioritizes_helmet_violation_with_explicit_styles():
    image = np.full((48, 76, 3), 255, dtype=np.uint8)
    detections = [
        Detection("no_helmet", 0.95, (4, 20, 20, 38)),
        Detection("person", 0.80, (28, 20, 44, 38)),
        Detection("helmet_worn", 0.88, (52, 20, 68, 38)),
    ]

    annotated = annotate(image, detections)

    assert tuple(annotated[24, 4]) == (38, 38, 220)
    assert tuple(annotated[24, 7]) == (38, 38, 220)
    assert tuple(annotated[24, 8]) == (255, 255, 255)
    assert tuple(annotated[24, 28]) == (138, 112, 86)
    assert tuple(annotated[24, 30]) == (255, 255, 255)
    assert tuple(annotated[24, 52]) == (102, 138, 76)
    assert tuple(annotated[24, 54]) == (102, 138, 76)


def test_annotation_label_falls_back_to_ascii_when_chinese_font_is_unavailable():
    assert annotation_label("no_helmet", supports_chinese=False) == "NO HELMET"
    assert annotation_label("helmet_worn", supports_chinese=False) == "HELMET OK"
    assert annotation_label("no_helmet", supports_chinese=True) == "未佩戴安全帽"


def test_worker_marks_detected_person_without_helmet_as_violation(batch_context):
    repository, storage, original_worker, owner_id = batch_context
    original_worker.stop()
    worker = BatchWorker(
        repository,
        DetectorProvider(PersonOnlyDetector(), model_version_id=8),
        storage,
        poll_interval=0.01,
    )
    job = _create_job(repository, storage, owner_id, [_jpeg_bytes()])

    assert worker.process_available() == 1

    result = repository.list_images(job.id)[0]
    assert result.detections == [
        {"label": "no_helmet", "confidence": 0.87, "bbox": [0, 0, 59, 39]}
    ]
    assert result.annotated_key is not None
    with storage.open(result.annotated_key) as stream:
        encoded = np.frombuffer(stream.read(), dtype=np.uint8)
    annotated = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    assert annotated is not None
    blue, green, red = (int(value) for value in annotated[20, 0])
    assert red > 200
    assert blue < 80
    assert green < 80


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


def test_unavailable_model_leaves_recovered_job_queued_then_wakes_on_replace(
    batch_context,
    monkeypatch,
):
    repository, storage, original_worker, owner_id = batch_context
    original_worker.stop()
    job = _create_job(repository, storage, owner_id, [_jpeg_bytes()])
    assert repository.claim_next_image() is not None
    provider = DetectorProvider(NoopDetector(), model_version_id=None)
    worker = BatchWorker(repository, provider, storage, poll_interval=10)

    class FailingActiveModelRepository:
        def get_active_model(self):
            return {"id": 8, "file_path": "opaque/broken.onnx"}

    monkeypatch.setattr(
        runtime_module,
        "build_detector",
        lambda _path: (_ for _ in ()).throw(RuntimeError("load failed")),
    )
    runtime = RuntimeManager(
        Settings(enable_runtime=False),
        FailingActiveModelRepository(),
        provider,
        EventHub(),
    )
    asyncio.run(runtime.start())

    worker.start()
    time.sleep(0.1)
    assert repository.get_job(job.id).status == "queued"
    assert repository.list_images(job.id)[0].status == "queued"

    provider.replace(FakeDetector(), model_version_id=8)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if repository.get_job(job.id).status == "completed":
            break
        time.sleep(0.01)
    worker.stop()

    assert repository.get_job(job.id).status == "completed"
    assert repository.list_images(job.id)[0].model_version_id == 8


def test_unavailable_worker_shutdown_interrupts_long_model_wait(batch_context):
    repository, storage, original_worker, _owner_id = batch_context
    original_worker.stop()
    worker = BatchWorker(
        repository,
        DetectorProvider(NoopDetector(), model_version_id=None),
        storage,
        poll_interval=10,
    )
    worker.start()
    time.sleep(0.05)

    started = time.monotonic()
    worker.stop()

    assert time.monotonic() - started < 0.5


def test_cleanup_and_processing_worker_coordinate_without_orphaned_output(
    batch_context,
):
    repository, storage, original_worker, owner_id = batch_context
    original_worker.stop()
    detector = BlockingFakeDetector()
    provider = DetectorProvider(detector, model_version_id=8)
    worker = BatchWorker(repository, provider, storage, poll_interval=0.01)
    cleaner = RetentionCleaner(repository, storage)
    job = _create_job(repository, storage, owner_id, [_jpeg_bytes()])

    worker.start()
    assert detector.started.wait(timeout=1)
    first_cleanup = cleaner.run_once(now=NOW + timedelta(days=30))

    assert first_cleanup.skipped_jobs == 1
    assert repository.get_job(job.id) is not None
    detector.release.set()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if repository.get_job(job.id).status == "completed":
            break
        time.sleep(0.01)
    worker.stop()
    image = repository.list_images(job.id)[0]
    assert image.annotated_key is not None

    second_cleanup = cleaner.run_once(now=NOW + timedelta(days=30))

    assert second_cleanup.deleted_jobs == 1
    assert repository.get_job(job.id) is None
    for key in (image.source_key, image.annotated_key):
        with pytest.raises(FileNotFoundError):
            storage.open(key)
