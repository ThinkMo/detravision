from __future__ import annotations

import logging
import asyncio
import threading
import time
from pathlib import Path

import pytest
import httpx
from fastapi import FastAPI

from backend.api import routes
from backend.auth import AuthenticatedUser
from backend.auth.dependencies import current_user
from backend.db import Repository
from backend.db.migrate import run_migrations
from backend.db.session import create_engine_from_url, create_session_factory
from backend.engine import Detection, DetectorProvider
from backend.model_activation import (
    ModelActivationConflict,
    ModelActivationService,
    ModelLoadFailed,
    ModelNotFound,
    ModelWarmupFailed,
)


class LabelDetector:
    def __init__(self, label: str, *, fail: bool = False) -> None:
        self.label = label
        self.fail = fail

    def predict(self, _image: object) -> list[Detection]:
        if self.fail:
            raise RuntimeError("private detector diagnostic")
        return [Detection(self.label, 0.9, (0.0, 0.0, 1.0, 1.0))]


@pytest.fixture
def activation_repository(tmp_path: Path):
    database_url = "sqlite:///" + (tmp_path / "activation.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    repository = Repository(create_session_factory(engine))
    old_path = tmp_path / "old.pt"
    candidate_path = tmp_path / "candidate.pt"
    old_path.write_bytes(b"old")
    candidate_path.write_bytes(b"candidate")
    old = repository.create_model(
        {"name": "old", "file_path": str(old_path), "classes": ["no_helmet"]}
    )
    candidate = repository.create_model(
        {"name": "candidate", "file_path": str(candidate_path), "classes": ["no_helmet"]}
    )
    repository.activate_model(old["id"])
    try:
        yield repository, old, candidate
    finally:
        engine.dispose()


def test_activation_loads_and_warms_before_persisting_then_swaps_provider(
    activation_repository,
):
    repository, old, candidate = activation_repository
    provider = DetectorProvider(LabelDetector("old"), old["id"])
    order: list[str] = []

    class WarmDetector(LabelDetector):
        warmed = False

        def predict(self, image: object) -> list[Detection]:
            if not self.warmed:
                order.append("warm")
                assert repository.get_active_model()["id"] == old["id"]
                self.warmed = True
            return super().predict(image)

    original_set_active = repository.set_active_model

    def track_persistence(model_id: int):
        order.append("persist")
        return original_set_active(model_id)

    repository.set_active_model = track_persistence  # type: ignore[method-assign]
    service = ModelActivationService(
        repository,
        provider,
        detector_builder=lambda _path: (order.append("load"), WarmDetector("new"))[1],
        warmup_image_factory=object,
    )

    activated = service.activate(candidate["id"])

    assert order == ["load", "warm", "persist"]
    assert activated["id"] == candidate["id"]
    assert activated["is_active"] is True
    assert repository.get_active_model()["id"] == candidate["id"]
    result = provider.predict_batch(object())
    assert result.model_version_id == candidate["id"]
    assert result.detections[0].label == "new"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [("load", ModelLoadFailed), ("warmup", ModelWarmupFailed), ("persist", ModelActivationConflict)],
)
def test_pre_swap_failure_preserves_database_and_provider(
    activation_repository,
    failure: str,
    expected: type[Exception],
):
    repository, old, candidate = activation_repository
    provider = DetectorProvider(LabelDetector("old"), old["id"])

    def build(_path: str):
        if failure == "load":
            raise RuntimeError("load failed")
        return LabelDetector("new", fail=failure == "warmup")

    if failure == "persist":
        repository.set_active_model = lambda _model_id: (_ for _ in ()).throw(  # type: ignore[method-assign]
            RuntimeError("database failed")
        )
    service = ModelActivationService(
        repository,
        provider,
        detector_builder=build,
        warmup_image_factory=object,
    )

    with pytest.raises(expected):
        service.activate(candidate["id"])

    assert repository.get_active_model()["id"] == old["id"]
    result = provider.predict_batch(object())
    assert result.model_version_id == old["id"]
    assert result.detections[0].label == "old"


def test_provider_replace_failure_restores_previous_database_model(
    activation_repository,
):
    repository, old, candidate = activation_repository

    class RejectingProvider:
        def __init__(self) -> None:
            self.detector: object = LabelDetector("old")
            self.model_version_id = old["id"]
            self.reject_next = True

        def snapshot(self) -> tuple[object, int]:
            return self.detector, self.model_version_id

        def replace(self, detector: object, model_version_id: int) -> None:
            self.detector = detector
            self.model_version_id = model_version_id
            if self.reject_next:
                self.reject_next = False
                raise RuntimeError(f"replace rejected {model_version_id}")

    provider = RejectingProvider()

    service = ModelActivationService(
        repository,
        provider,  # type: ignore[arg-type]
        detector_builder=lambda _path: LabelDetector("new"),
        warmup_image_factory=object,
    )

    with pytest.raises(ModelActivationConflict):
        service.activate(candidate["id"])

    assert repository.get_active_model()["id"] == old["id"]
    assert provider.model_version_id == old["id"]
    assert isinstance(provider.detector, LabelDetector)
    assert provider.detector.label == "old"


def test_activation_operations_are_serialized(activation_repository):
    repository, old, first = activation_repository
    second_path = Path(first["file_path"]).with_name("second.pt")
    second_path.write_bytes(b"second")
    second = repository.create_model(
        {"name": "second", "file_path": str(second_path), "classes": ["no_helmet"]}
    )
    provider = DetectorProvider(LabelDetector("old"), old["id"])
    counter_lock = threading.Lock()
    builders_active = 0
    max_builders_active = 0

    def build(path: str) -> LabelDetector:
        nonlocal builders_active, max_builders_active
        with counter_lock:
            builders_active += 1
            max_builders_active = max(max_builders_active, builders_active)
        time.sleep(0.03)
        with counter_lock:
            builders_active -= 1
        return LabelDetector(Path(path).stem)

    service = ModelActivationService(
        repository,
        provider,
        detector_builder=build,
        warmup_image_factory=object,
    )
    threads = [
        threading.Thread(target=service.activate, args=(model_id,))
        for model_id in (first["id"], second["id"])
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert max_builders_active == 1
    active = repository.get_active_model()
    assert provider.predict_batch(object()).model_version_id == active["id"]


def test_successful_activation_synchronizes_runtime_signature(
    activation_repository,
):
    repository, old, candidate = activation_repository
    provider = DetectorProvider(LabelDetector("old"), old["id"])

    class Runtime:
        def __init__(self) -> None:
            self.signature: tuple[int, str] | None = None

        def set_model_signature(self, model: dict[str, object]) -> None:
            self.signature = (int(model["id"]), str(model["file_path"]))

    runtime = Runtime()
    service = ModelActivationService(
        repository,
        provider,
        detector_builder=lambda _path: LabelDetector("new"),
        warmup_image_factory=object,
        runtime=runtime,
    )

    service.activate(candidate["id"])

    assert runtime.signature == (candidate["id"], candidate["file_path"])


def test_missing_model_and_failures_never_expose_model_path(
    activation_repository,
    caplog,
):
    repository, old, candidate = activation_repository
    secret_path = candidate["file_path"]
    provider = DetectorProvider(LabelDetector("old"), old["id"])
    service = ModelActivationService(
        repository,
        provider,
        detector_builder=lambda _path: (_ for _ in ()).throw(
            RuntimeError(f"cannot load {secret_path}")
        ),
        warmup_image_factory=object,
    )
    caplog.set_level(logging.INFO, logger="backend.model_activation")

    with pytest.raises(ModelLoadFailed) as captured:
        service.activate(candidate["id"])
    with pytest.raises(ModelNotFound):
        service.activate(999999)

    assert str(captured.value) == "model_load_failed"
    assert secret_path not in caplog.text


@pytest.mark.parametrize(
    ("failure", "status_code"),
    [
        (ModelNotFound(), 404),
        (ModelLoadFailed(), 503),
        (ModelWarmupFailed(), 503),
        (ModelActivationConflict(), 409),
    ],
)
def test_activation_route_uses_app_service_and_maps_safe_failures(
    failure: Exception,
    status_code: int,
):
    class FailingService:
        def activate(self, _model_id: int):
            raise failure

    class Audit:
        def record(self, *_args, **_kwargs) -> None:
            return None

    class LegacyRepository:
        def activate_model(self, model_id: int):
            return {
                "id": model_id,
                "name": "legacy",
                "file_path": "private.pt",
                "runtime": "pt",
                "classes": [],
                "map50": None,
                "is_active": True,
                "created_at": "2026-08-22T00:00:00",
            }

    app = FastAPI()
    app.include_router(routes.router)
    app.state.model_activation_service = FailingService()
    app.state.audit_service = Audit()
    app.state.repository = LegacyRepository()
    app.dependency_overrides[current_user] = lambda: AuthenticatedUser(
        id=1,
        username="admin",
        role="admin",
        must_change_password=False,
    )

    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post("/api/models/42/activate")

    response = asyncio.run(send())
    assert response.status_code == status_code
    assert response.json() == {"detail": str(failure)}
