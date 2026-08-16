from __future__ import annotations

import io
import logging
import threading
from collections.abc import Callable

import cv2
import numpy as np

from backend.engine import Detection, DetectorProvider
from backend.storage import FileStorage

from .annotate import annotate
from .repository import BatchRepository
from .types import ClaimedImage, CompletedImage


logger = logging.getLogger(__name__)


class BatchWorker:
    """Process queued images serially through the shared, priority-aware detector."""

    def __init__(
        self,
        repository: BatchRepository,
        provider: DetectorProvider,
        storage: FileStorage,
        *,
        poll_interval: float = 0.5,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._repository = repository
        self._provider = provider
        self._storage = storage
        self._poll_interval = poll_interval
        self._thread_factory = thread_factory
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._repository.recover_interrupted()
        self._stop_event.clear()
        self._thread = self._thread_factory(
            target=self._run,
            name="batch-inference-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._provider.wake_waiters()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(1.0, self._poll_interval * 2))
        self._thread = None

    def process_available(self) -> int:
        processed = 0
        while not self._stop_event.is_set():
            if not self._provider.available:
                break
            claimed = self._repository.claim_next_image()
            if claimed is None:
                break
            self._process(claimed)
            processed += 1
        return processed

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if not self._provider.wait_until_available(
                self._stop_event,
                timeout=self._poll_interval,
            ):
                continue
            processed = self.process_available()
            if not processed:
                self._stop_event.wait(self._poll_interval)

    def _process(self, claimed: ClaimedImage) -> None:
        try:
            try:
                with self._storage.open(claimed.source_key) as stream:
                    encoded = np.frombuffer(stream.read(), dtype=np.uint8)
            except Exception:
                self._fail(claimed, "storage_read_failed", "source image is unavailable")
                return

            image = cv2.imdecode(encoded, cv2.IMREAD_COLOR) if encoded.size else None
            if image is None:
                self._fail(claimed, "decode_failed", "image could not be decoded")
                return

            try:
                prediction = self._provider.predict_batch(image)
            except Exception:
                self._fail(claimed, "inference_failed", "image inference failed")
                return

            try:
                rendered = annotate(image, prediction.detections)
                encoded_ok, output = cv2.imencode(
                    ".jpg",
                    rendered,
                    [cv2.IMWRITE_JPEG_QUALITY, 90],
                )
                if not encoded_ok:
                    raise ValueError("jpeg encode failed")
            except Exception:
                self._fail(claimed, "annotation_failed", "image annotation failed")
                return

            try:
                annotated_key = self._storage.save(
                    io.BytesIO(output.tobytes()), "batch-result", "jpg"
                )
            except Exception:
                self._fail(claimed, "storage_write_failed", "result image could not be saved")
                return

            result = CompletedImage(
                annotated_key=annotated_key,
                model_version_id=prediction.model_version_id,
                detections=[self._detection_dict(item) for item in prediction.detections],
            )
            try:
                self._repository.complete_image(claimed.id, result)
            except Exception:
                try:
                    self._storage.delete(annotated_key)
                except Exception:
                    logger.warning(
                        "batch_output_cleanup_failed key=%s error_code=storage_delete_failed",
                        annotated_key,
                    )
                self._fail(
                    claimed,
                    "completion_failed",
                    "image result could not be committed",
                )
        except Exception:
            self._fail(claimed, "processing_failed", "image processing failed")

    def _fail(self, claimed: ClaimedImage, code: str, message: str) -> None:
        logger.warning(
            "batch_image_failed image_id=%s source_key=%s error_code=%s",
            claimed.id,
            claimed.source_key,
            code,
        )
        try:
            self._repository.fail_image(claimed.id, code, message)
        except Exception:
            logger.error(
                "batch_failure_commit_failed image_id=%s error_code=%s",
                claimed.id,
                code,
            )

    @staticmethod
    def _detection_dict(detection: Detection) -> dict[str, object]:
        return {
            "label": detection.label,
            "confidence": detection.confidence,
            "bbox": list(detection.bbox),
        }
