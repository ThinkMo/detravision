from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from backend.db import Repository
from backend.engine import Detector, DetectorProvider, build_detector

logger = logging.getLogger(__name__)


class ModelActivationError(RuntimeError):
    code = "model_activation_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


class ModelNotFound(ModelActivationError):
    code = "model_not_found"


class ModelLoadFailed(ModelActivationError):
    code = "model_load_failed"


class ModelWarmupFailed(ModelActivationError):
    code = "model_warmup_failed"


class ModelActivationConflict(ModelActivationError):
    code = "model_activation_conflict"


def _warmup_image() -> Any:
    import numpy as np

    return np.zeros((64, 64, 3), dtype=np.uint8)


class ModelActivationService:
    """Load and warm a model before switching persistence and inference."""

    def __init__(
        self,
        repository: Repository,
        provider: DetectorProvider,
        *,
        detector_builder: Callable[[str], Detector] = build_detector,
        warmup_image_factory: Callable[[], Any] = _warmup_image,
        runtime: Any | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._detector_builder = detector_builder
        self._warmup_image_factory = warmup_image_factory
        self._runtime = runtime
        self._lock = threading.Lock()

    def activate(self, model_id: int) -> dict[str, Any]:
        with self._lock:
            candidate = self._repository.get_model(model_id)
            if candidate is None:
                raise ModelNotFound()
            try:
                detector = self._detector_builder(candidate["file_path"])
            except Exception as exc:
                self._log_rejection(model_id, "load", exc)
                raise ModelLoadFailed() from None
            try:
                detector.predict(self._warmup_image_factory())
            except Exception as exc:
                self._log_rejection(model_id, "warmup", exc)
                raise ModelWarmupFailed() from None
            previous_detector, previous_provider_id = self._provider.snapshot()
            try:
                previous = self._repository.set_active_model(model_id)
            except LookupError:
                raise ModelNotFound() from None
            except Exception as exc:
                self._log_rejection(model_id, "persist", exc)
                raise ModelActivationConflict() from None
            try:
                self._provider.replace(detector, model_version_id=model_id)
            except Exception as exc:
                try:
                    self._provider.replace(
                        previous_detector,
                        model_version_id=previous_provider_id,
                    )
                except Exception as rollback_exc:
                    logger.error(
                        "model_provider_rollback_failed model_id=%s error_type=%s",
                        model_id,
                        type(rollback_exc).__name__,
                    )
                try:
                    self._repository.restore_active_model(
                        previous["id"] if previous is not None else None
                    )
                except Exception as rollback_exc:
                    logger.error(
                        "model_activation_rollback_failed model_id=%s error_type=%s",
                        model_id,
                        type(rollback_exc).__name__,
                    )
                self._log_rejection(model_id, "replace", exc)
                raise ModelActivationConflict() from None
            activated = {**candidate, "is_active": True}
            if self._runtime is not None:
                self._runtime.set_model_signature(activated)
            logger.info("model_activation_succeeded model_id=%s", model_id)
            return activated

    @staticmethod
    def _log_rejection(model_id: int, phase: str, exc: Exception) -> None:
        logger.warning(
            "model_activation_rejected model_id=%s phase=%s error_type=%s",
            model_id,
            phase,
            type(exc).__name__,
        )
