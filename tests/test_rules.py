import asyncio

import cv2
import numpy as np

from backend.core.config import Settings
from backend.engine import Detection, PredictionResult
from backend.pipeline.runtime import RuntimeManager
from backend.rules import RuleEngine, SemanticEvent
from backend.stream import Frame
from backend.ws import EventHub


class RecordingProvider:
    def __init__(self) -> None:
        self.realtime_images: list[object] = []

    def predict_realtime(self, image: object) -> PredictionResult:
        self.realtime_images.append(image)
        return PredictionResult([], model_version_id=11)


class CameraRepository:
    def get_camera(self, camera_id: int) -> dict[str, object]:
        return {
            "id": camera_id,
            "enabled": True,
            "roi": [],
            "detect_classes": [],
        }


class RecordingHub:
    def __init__(self) -> None:
        self.frames: list[tuple[str, bytes]] = []

    async def broadcast_bytes(self, channel: str, payload: bytes) -> None:
        self.frames.append((channel, payload))


def test_no_helmet_is_associated_with_head_region_and_debounced():
    engine = RuleEngine(required_hits=3, window_seconds=5, cooldown_seconds=30)
    detections = [
        Detection("person", 0.9, (0, 0, 100, 200)),
        Detection("no_helmet", 0.8, (25, 10, 75, 60)),
    ]
    assert engine.evaluate(1, detections, now=0) == []
    assert engine.evaluate(1, detections, now=1) == []
    result = engine.evaluate(1, detections, now=2)
    assert len(result) == 1
    assert result[0].event_type == "no_helmet"
    assert engine.evaluate(1, detections, now=3) == []


def test_person_without_detected_helmet_emits_no_helmet_after_debounce():
    engine = RuleEngine(required_hits=3, window_seconds=5, cooldown_seconds=30)
    detections = [Detection("person", 0.87, (0, 0, 100, 200))]

    assert engine.evaluate(1, detections, now=0) == []
    assert engine.evaluate(1, detections, now=1) == []
    result = engine.evaluate(1, detections, now=2)

    assert result == [SemanticEvent("no_helmet", 0.87, (0, 0, 100, 200))]


def test_person_with_helmet_in_head_region_does_not_emit_violation():
    engine = RuleEngine(required_hits=1)
    detections = [
        Detection("person", 0.91, (0, 0, 100, 200)),
        Detection("helmet", 0.84, (25, 5, 75, 55)),
    ]

    assert engine.evaluate(1, detections, now=0) == []


def test_roi_filters_fire_outside_polygon():
    engine = RuleEngine(required_hits=1)
    fire = [Detection("fire", 0.95, (200, 200, 220, 220))]
    result = engine.evaluate(1, fire, roi=[[0, 0], [100, 0], [100, 100], [0, 100]], now=0)
    assert result == []


def test_runtime_routes_frames_through_realtime_provider():
    provider = RecordingProvider()
    manager = RuntimeManager(
        Settings(enable_runtime=False),
        CameraRepository(),
        provider,
        EventHub(),
    )
    image = object()

    manager._process_frame(Frame(camera_id=7, captured_at=1.0, image=image))

    assert provider.realtime_images == [image]
    assert manager.processed_frames == 1


def test_runtime_preview_draws_person_without_helmet_as_red_violation():
    hub = RecordingHub()
    manager = RuntimeManager(
        Settings(enable_runtime=False),
        CameraRepository(),
        RecordingProvider(),
        hub,
    )
    image = np.full((40, 60, 3), 240, dtype=np.uint8)
    frame = Frame(camera_id=7, captured_at=1.0, image=image)

    async def exercise() -> None:
        manager.loop = asyncio.get_running_loop()
        manager._publish_preview(
            frame,
            [Detection("person", 0.87, (0, 0, 59, 39))],
        )
        await asyncio.sleep(0)

    asyncio.run(exercise())

    assert len(hub.frames) == 1
    channel, payload = hub.frames[0]
    assert channel == "live:7"
    rendered = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert rendered is not None
    blue, green, red = (int(value) for value in rendered[20, 0])
    assert red > 180
    assert red > blue * 2
    assert red > green * 2
