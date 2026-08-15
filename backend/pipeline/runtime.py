from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any

from backend.core.config import Settings
from backend.core.notifier import FeishuNotifier
from backend.db import Database
from backend.engine import Detector, build_detector
from backend.rules import RuleEngine
from backend.stream.capture import CaptureWorker
from backend.stream.frame_queue import DropOldestQueue, Frame
from backend.ws import EventHub

logger = logging.getLogger(__name__)


class RuntimeManager:
    def __init__(self, settings: Settings, db: Database, detector: Detector, hub: EventHub):
        self.settings = settings
        self.db = db
        self.detector = detector
        self.hub = hub
        self.frames = DropOldestQueue(settings.queue_size)
        self.rules = RuleEngine()
        self.notifier = FeishuNotifier(settings.feishu_webhook)
        self.captures: dict[int, CaptureWorker] = {}
        self.camera_signatures: dict[int, tuple[Any, ...]] = {}
        self.stop_event = threading.Event()
        self.workers: list[threading.Thread] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.reconcile_task: asyncio.Task | None = None
        self.processed_frames = 0
        self.created_events = 0
        self.model_signature: tuple[int, str] | None = None

    async def start(self) -> None:
        if not self.settings.enable_runtime:
            return
        self.loop = asyncio.get_running_loop()
        self.settings.snapshot_dir.mkdir(parents=True, exist_ok=True)
        for index in range(max(1, self.settings.worker_count)):
            thread = threading.Thread(target=self._process, name=f"inference-{index}", daemon=True)
            thread.start()
            self.workers.append(thread)
        self.reconcile_task = asyncio.create_task(self._reconcile_loop(), name="camera-reconciler")

    async def stop(self) -> None:
        if self.reconcile_task:
            self.reconcile_task.cancel()
            await asyncio.gather(self.reconcile_task, return_exceptions=True)
        for capture in tuple(self.captures.values()):
            capture.stop()
        self.captures.clear()
        self.stop_event.set()
        for thread in self.workers:
            thread.join(timeout=3)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.enable_runtime,
            "active_cameras": sorted(self.captures),
            "queued_frames": self.frames.qsize(),
            "dropped_frames": self.frames.dropped,
            "processed_frames": self.processed_frames,
            "created_events": self.created_events,
            "capture_errors": {
                str(camera_id): capture.last_error
                for camera_id, capture in self.captures.items()
                if capture.last_error
            },
        }

    @staticmethod
    def _signature(camera: dict[str, Any]) -> tuple[Any, ...]:
        return (camera["rtsp_url"], camera["sample_fps"], tuple(map(tuple, camera["roi"])), tuple(camera["detect_classes"]))

    async def _reconcile_loop(self) -> None:
        while True:
            self._reconcile_model()
            enabled = {camera["id"]: camera for camera in self.db.list("camera", where="enabled = 1")}
            for camera_id in set(self.captures) - set(enabled):
                self.captures.pop(camera_id).stop()
                self.camera_signatures.pop(camera_id, None)
            for camera_id, camera in enabled.items():
                signature = self._signature(camera)
                if self.camera_signatures.get(camera_id) == signature:
                    continue
                if camera_id in self.captures:
                    self.captures.pop(camera_id).stop()
                capture = CaptureWorker(camera, self.frames.put)
                capture.start()
                self.captures[camera_id] = capture
                self.camera_signatures[camera_id] = signature
            await asyncio.sleep(self.settings.reconcile_interval)

    def _reconcile_model(self) -> None:
        active = self.db.list("model_version", where="is_active = 1")
        if not active:
            return
        model = active[0]
        signature = (model["id"], model["file_path"])
        if signature == self.model_signature:
            return
        try:
            replacement = build_detector(model["file_path"])
        except Exception:
            logger.exception("Unable to activate model %s; keeping current detector", model["id"])
            return
        self.detector = replacement
        self.model_signature = signature
        logger.info("Activated model %s from %s", model["id"], model["file_path"])

    def _process(self) -> None:
        while not self.stop_event.is_set():
            try:
                frame = self.frames.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process_frame(frame)
            except Exception:
                logger.exception("Frame processing failed for camera %s", frame.camera_id)
            finally:
                self.frames.task_done()

    def _process_frame(self, frame: Frame) -> None:
        camera = self.db.get("camera", frame.camera_id)
        if not camera or not camera["enabled"]:
            return
        detections = self.detector.predict(frame.image)
        events = self.rules.evaluate(
            frame.camera_id,
            detections,
            roi=camera["roi"],
            allowed_classes=camera["detect_classes"],
        )
        self.processed_frames += 1
        self._publish_preview(frame)
        for event in events:
            snapshot_path = self._save_snapshot(frame)
            record = self.db.create(
                "detection_event",
                {
                    "camera_id": frame.camera_id,
                    "event_type": event.event_type,
                    "confidence": event.confidence,
                    "bbox": list(event.bbox),
                    "snapshot_path": snapshot_path,
                    "status": "new",
                },
            )
            self.created_events += 1
            self.notifier.send(record)
            if self.loop:
                asyncio.run_coroutine_threadsafe(self.hub.broadcast("events", record), self.loop)

    def _publish_preview(self, frame: Frame) -> None:
        if not self.loop:
            return
        try:
            import cv2

            ok, encoded = cv2.imencode(".jpg", frame.image, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if ok:
                asyncio.run_coroutine_threadsafe(
                    self.hub.broadcast_bytes(f"live:{frame.camera_id}", encoded.tobytes()), self.loop
                )
        except ImportError:
            return

    def _save_snapshot(self, frame: Frame) -> str | None:
        try:
            import cv2

            name = f"camera-{frame.camera_id}-{int(frame.captured_at * 1000)}.jpg"
            path = Path(self.settings.snapshot_dir) / name
            return str(path) if cv2.imwrite(str(path), frame.image) else None
        except ImportError:
            return None
