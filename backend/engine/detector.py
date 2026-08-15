from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Detection:
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]


class Detector(Protocol):
    def predict(self, image: Any) -> list[Detection]: ...


class NoopDetector:
    def predict(self, image: Any) -> list[Detection]:
        return []


class UltralyticsDetector:
    def __init__(self, model_path: str):
        from ultralytics import YOLO

        self.model = YOLO(model_path)

    def predict(self, image: Any) -> list[Detection]:
        result = self.model.predict(image, verbose=False)[0]
        names = result.names
        detections = []
        for box in result.boxes:
            detections.append(
                Detection(
                    label=str(names[int(box.cls.item())]),
                    confidence=float(box.conf.item()),
                    bbox=tuple(float(value) for value in box.xyxy[0].tolist()),
                )
            )
        return detections


def build_detector(model_path: str | None) -> Detector:
    if not model_path:
        return NoopDetector()
    if not Path(model_path).exists():
        raise FileNotFoundError(f"model does not exist: {model_path}")
    return UltralyticsDetector(model_path)
