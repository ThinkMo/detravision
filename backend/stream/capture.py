from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from backend.stream.frame_queue import Frame

logger = logging.getLogger(__name__)


class CaptureWorker:
    def __init__(self, camera: dict, on_frame: Callable[[Frame], None]):
        self.camera = camera
        self.on_frame = on_frame
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name=f"camera-{camera['id']}", daemon=True)
        self.last_error: str | None = None

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=3)

    def _run(self) -> None:
        try:
            import cv2  # Optional until an enabled camera is configured.
        except ImportError:
            self.last_error = "opencv-python-headless is not installed"
            logger.error(self.last_error)
            return

        interval = 1.0 / float(self.camera["sample_fps"])
        while not self.stop_event.is_set():
            capture = cv2.VideoCapture(self.camera["rtsp_url"])
            if not capture.isOpened():
                self.last_error = "unable to open stream"
                self.stop_event.wait(3)
                continue
            last_emitted = 0.0
            while not self.stop_event.is_set():
                ok, image = capture.read()
                if not ok:
                    self.last_error = "stream disconnected"
                    break
                now = time.monotonic()
                if now - last_emitted >= interval:
                    self.on_frame(Frame(self.camera["id"], time.time(), image))
                    last_emitted = now
            capture.release()
            self.stop_event.wait(1)
