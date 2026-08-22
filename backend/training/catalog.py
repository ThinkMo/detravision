from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from backend.core.config import Settings


MODEL_IDS = tuple(
    f"yolo{series}{size}.pt"
    for series in ("11", "26")
    for size in ("n", "s", "m", "l", "x")
)

_IMAGE_SIZES = frozenset({640, 960})
_BATCHES = frozenset({"1", "2", "4", "8", "auto"})
_DEVICES = frozenset({"auto", "cpu", "0"})


@dataclass(frozen=True, slots=True)
class TrainingRequest:
    base_model: str
    imgsz: int
    epochs: int
    batch: str
    device: str
    patience: int
    executor: str = "local"

    def __post_init__(self) -> None:
        if not isinstance(self.executor, str) or self.executor != "local":
            raise ValueError("executor is unavailable")
        if not isinstance(self.base_model, str) or self.base_model not in MODEL_IDS:
            raise ValueError("unsupported base model")
        if type(self.imgsz) is not int or self.imgsz not in _IMAGE_SIZES:
            raise ValueError("unsupported image size")
        if type(self.epochs) is not int or not 1 <= self.epochs <= 1000:
            raise ValueError("epochs must be between 1 and 1000")
        if not isinstance(self.batch, str) or self.batch not in _BATCHES:
            raise ValueError("unsupported batch size")
        if not isinstance(self.device, str) or self.device not in _DEVICES:
            raise ValueError("unsupported device")
        if type(self.patience) is not int or not 0 <= self.patience <= 500:
            raise ValueError("patience must be between 0 and 500")


def _dataset_summary(settings: Settings) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "id": "configured",
        "available": False,
        "has_test_split": False,
        "classes": [],
    }
    try:
        payload = yaml.safe_load(settings.training_dataset_yaml.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return summary
    if not isinstance(payload, dict):
        return summary

    names = payload.get("names")
    if isinstance(names, dict):
        try:
            classes = [
                str(name)
                for _, name in sorted(names.items(), key=lambda item: int(item[0]))
            ]
        except (TypeError, ValueError):
            classes = []
    elif isinstance(names, list):
        classes = [str(name) for name in names]
    else:
        classes = []
    return {
        "id": "configured",
        "available": True,
        "has_test_split": bool(payload.get("test")),
        "classes": classes,
    }


def training_catalog(settings: Settings) -> dict[str, Any]:
    return {
        "models": [
            {
                "id": model_id,
                "series": model_id[4:6],
                "size": model_id[6],
            }
            for model_id in MODEL_IDS
        ],
        "executors": [
            {"id": "local", "enabled": settings.training_enabled},
            {"id": "remote", "enabled": False},
        ],
        "dataset": _dataset_summary(settings),
    }
