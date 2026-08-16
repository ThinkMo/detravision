from __future__ import annotations

import threading
import time

import pytest

from backend.engine import Detection, DetectorProvider, NoopDetector


class FakeDetector:
    def __init__(self, label: str) -> None:
        self.label = label

    def predict(self, image: object) -> list[Detection]:
        return [Detection(self.label, 0.9, (0.0, 0.0, 1.0, 1.0))]


class BlockingDetector:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.order: list[str] = []
        self._calls = 0
        self._lock = threading.Lock()

    def predict(self, image: object) -> list[Detection]:
        with self._lock:
            self._calls += 1
            call_number = self._calls
            self.order.append(str(image))
        if call_number == 1:
            self.started.set()
            assert self.release.wait(timeout=2)
        return []


class FailingDetector:
    def predict(self, image: object) -> list[Detection]:
        raise RuntimeError("prediction failed")


def _wait_until(predicate, *, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not met before timeout")


def test_replace_is_atomic_and_result_records_version():
    provider = DetectorProvider(FakeDetector("helmet"), model_version_id=3)

    first = provider.predict_batch(object())
    provider.replace(FakeDetector("no_helmet"), model_version_id=4)
    second = provider.predict_batch(object())

    assert first.model_version_id == 3
    assert first.detections[0].label == "helmet"
    assert second.model_version_id == 4
    assert second.detections[0].label == "no_helmet"


def test_waiting_realtime_prediction_runs_before_next_batch_prediction():
    detector = BlockingDetector()
    provider = DetectorProvider(detector, model_version_id=1)

    batch_1 = threading.Thread(target=provider.predict_batch, args=("batch-1",))
    realtime = threading.Thread(target=provider.predict_realtime, args=("realtime",))
    batch_2 = threading.Thread(target=provider.predict_batch, args=("batch-2",))
    batch_1.start()
    assert detector.started.wait(timeout=2)
    realtime.start()
    _wait_until(lambda: provider._realtime_waiters == 1)
    batch_2.start()
    detector.release.set()

    for thread in (batch_1, realtime, batch_2):
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert detector.order == ["batch-1", "realtime", "batch-2"]


def test_failed_prediction_releases_slot_for_replacement():
    provider = DetectorProvider(FailingDetector(), model_version_id=1)

    with pytest.raises(RuntimeError, match="prediction failed"):
        provider.predict_batch(object())

    provider.replace(FakeDetector("helmet"), model_version_id=2)
    result = provider.predict_realtime(object())
    assert result.model_version_id == 2
    assert result.detections[0].label == "helmet"


def test_available_is_false_only_for_noop_detector():
    provider = DetectorProvider(NoopDetector(), model_version_id=None)
    assert provider.available is False

    provider.replace(FakeDetector("helmet"), model_version_id=8)
    assert provider.available is True
