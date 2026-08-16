from __future__ import annotations

import asyncio
import logging

import cv2
import numpy as np

from backend.core.config import Settings
from backend.engine import Detection, DetectorProvider, NoopDetector, PredictionResult
from backend.pipeline import runtime as runtime_module
from backend.pipeline.runtime import RuntimeManager
from backend.ws import EventHub


class ActiveModelRepository:
    def __init__(self, model: dict[str, object]) -> None:
        self.model = model

    def get_active_model(self) -> dict[str, object]:
        return self.model


class PreviewRepository:
    def get_camera(self, camera_id: int) -> dict[str, object]:
        return {
            "id": camera_id,
            "enabled": True,
            "roi": [[0, 0], [80, 0], [80, 64], [0, 64]],
            "detect_classes": ["person", "no_helmet"],
        }


class PreviewProvider:
    def __init__(self, detections: list[Detection]) -> None:
        self.detections = detections

    def predict_realtime(self, _image: np.ndarray) -> PredictionResult:
        return PredictionResult(self.detections, model_version_id=11)


class RecordingRules:
    def __init__(self) -> None:
        self.calls: list[tuple[int, list[Detection], object, object]] = []

    def evaluate(
        self,
        camera_id: int,
        detections: list[Detection],
        *,
        roi: object,
        allowed_classes: object,
    ) -> list[object]:
        self.calls.append((camera_id, detections, roi, allowed_classes))
        return []


class RecordingHub:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes]] = []

    async def broadcast_bytes(self, channel: str, payload: bytes) -> None:
        self.messages.append((channel, payload))


def test_model_reconciliation_logs_stable_fields_without_local_path(
    tmp_path,
    monkeypatch,
    caplog,
):
    """Catches model paths or exception messages leaking on failure or success."""
    secret_path = str(tmp_path / "private-model-secret.onnx")
    repository = ActiveModelRepository({"id": 17, "file_path": secret_path})
    provider = DetectorProvider(NoopDetector(), model_version_id=None)
    manager = RuntimeManager(
        Settings(enable_runtime=False),
        repository,
        provider,
        EventHub(),
    )
    caplog.set_level(logging.INFO, logger=runtime_module.__name__)

    def fail_to_build(_path: str):
        raise RuntimeError(f"detector rejected {secret_path}")

    monkeypatch.setattr(runtime_module, "build_detector", fail_to_build)
    manager._reconcile_model()
    failure = [
        record
        for record in caplog.records
        if record.name == runtime_module.__name__
    ]
    assert [record.getMessage() for record in failure] == [
        "model_activation_failed model_id=17 error_type=RuntimeError"
    ]
    assert failure[0].exc_info is None
    assert secret_path not in caplog.text

    caplog.clear()
    marker = object()

    class ReplacementDetector:
        def predict(self, _image: object) -> list[object]:
            return [marker]

    replacement = ReplacementDetector()
    monkeypatch.setattr(runtime_module, "build_detector", lambda _path: replacement)
    manager._reconcile_model()
    success = [
        record
        for record in caplog.records
        if record.name == runtime_module.__name__
    ]
    assert [record.getMessage() for record in success] == [
        "model_activation_succeeded model_id=17"
    ]
    assert secret_path not in caplog.text
    result = provider.predict_batch(object())
    assert result.detections == [marker]
    assert result.model_version_id == 17


def test_start_loads_active_model_even_when_camera_runtime_is_disabled(
    tmp_path,
    monkeypatch,
):
    """Batch inference must receive the DB-active model before start returns."""
    model_path = str(tmp_path / "active.onnx")
    repository = ActiveModelRepository({"id": 23, "file_path": model_path})
    provider = DetectorProvider(NoopDetector(), model_version_id=None)
    manager = RuntimeManager(
        Settings(enable_runtime=False),
        repository,
        provider,
        EventHub(),
    )

    class ActiveDetector:
        def predict(self, _image: object) -> list[object]:
            return []

    monkeypatch.setattr(runtime_module, "build_detector", lambda _path: ActiveDetector())

    asyncio.run(manager.start())

    assert provider.available is True
    assert provider.predict_batch(object()).model_version_id == 23


def test_live_preview_broadcasts_annotated_jpeg_without_changing_rule_inputs(
    monkeypatch,
):
    detections = [Detection("no_helmet", 0.95, (20, 20, 60, 50))]
    provider = PreviewProvider(detections)
    repository = PreviewRepository()
    hub = RecordingHub()
    manager = RuntimeManager(Settings(enable_runtime=False), repository, provider, hub)
    manager.rules = rules = RecordingRules()
    manager.loop = object()
    raw = np.full((64, 80, 3), 255, dtype=np.uint8)

    def run_broadcast(coroutine: object, _loop: object) -> None:
        asyncio.run(coroutine)

    monkeypatch.setattr(runtime_module.asyncio, "run_coroutine_threadsafe", run_broadcast)

    frame = runtime_module.Frame(camera_id=9, captured_at=1.0, image=raw)
    manager._process_frame(frame)

    assert rules.calls == [
        (9, detections, [[0, 0], [80, 0], [80, 64], [0, 64]], ["person", "no_helmet"])
    ]
    assert rules.calls[0][1] is detections
    assert len(hub.messages) == 1
    channel, payload = hub.messages[0]
    preview = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert preview is not None
    assert channel == "live:9"
    assert not np.array_equal(preview[40, 20], raw[40, 20])
    assert int(np.abs(preview[40, 20].astype(int) - raw[40, 20].astype(int)).max()) > 100
    assert np.array_equal(frame.image, raw)
