from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from .detector import Detection, Detector, NoopDetector


@dataclass(frozen=True, slots=True)
class PredictionResult:
    detections: list[Detection]
    model_version_id: int | None


class DetectorProvider:
    """Serialize detector access while giving queued real-time work priority."""

    def __init__(self, detector: Detector, model_version_id: int | None) -> None:
        self._condition = threading.Condition()
        self._detector = detector
        self._model_version_id = model_version_id
        self._active = False
        self._realtime_waiters = 0

    def predict_realtime(self, image: Any) -> PredictionResult:
        with self._condition:
            self._realtime_waiters += 1
            try:
                self._condition.wait_for(lambda: not self._active)
                self._active = True
                detector = self._detector
                version = self._model_version_id
            finally:
                self._realtime_waiters -= 1
        return self._finish_prediction(detector, version, image)

    def predict_batch(self, image: Any) -> PredictionResult:
        with self._condition:
            self._condition.wait_for(
                lambda: not self._active and self._realtime_waiters == 0
            )
            self._active = True
            detector = self._detector
            version = self._model_version_id
        return self._finish_prediction(detector, version, image)

    def replace(self, detector: Detector, model_version_id: int | None) -> None:
        with self._condition:
            self._condition.wait_for(lambda: not self._active)
            self._detector = detector
            self._model_version_id = model_version_id
            self._condition.notify_all()

    def wait_until_available(
        self,
        stop_event: threading.Event,
        *,
        timeout: float | None = None,
    ) -> bool:
        """Wait without spinning until a usable detector is installed or stopped."""
        with self._condition:
            self._condition.wait_for(
                lambda: stop_event.is_set()
                or not isinstance(self._detector, NoopDetector),
                timeout=timeout,
            )
            return not stop_event.is_set() and not isinstance(
                self._detector, NoopDetector
            )

    def wake_waiters(self) -> None:
        with self._condition:
            self._condition.notify_all()

    @property
    def available(self) -> bool:
        with self._condition:
            return not isinstance(self._detector, NoopDetector)

    def _finish_prediction(
        self,
        detector: Detector,
        version: int | None,
        image: Any,
    ) -> PredictionResult:
        try:
            return PredictionResult(detector.predict(image), version)
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()
