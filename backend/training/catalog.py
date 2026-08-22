from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from backend.core.config import Settings


MAX_DATASET_YAML_BYTES = 1024 * 1024

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
        with settings.training_dataset_yaml.open("rb") as stream:
            raw_yaml = stream.read(MAX_DATASET_YAML_BYTES + 1)
        if len(raw_yaml) > MAX_DATASET_YAML_BYTES:
            return summary
        payload = yaml.safe_load(raw_yaml.decode("utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError, MemoryError):
        return summary
    if not isinstance(payload, Mapping):
        return summary

    splits_are_valid = all(
        isinstance(payload.get(split), str) and bool(payload[split].strip())
        for split in ("train", "val", "test")
    )

    names = payload.get("names")
    if isinstance(names, Mapping) and names:
        try:
            classes = [
                str(name)
                for _, name in sorted(names.items(), key=lambda item: int(item[0]))
            ]
        except (TypeError, ValueError):
            classes = []
    elif isinstance(names, list) and names:
        classes = [str(name) for name in names]
    else:
        classes = []
    if not splits_are_valid or not classes:
        return summary
    return {
        "id": "configured",
        "available": True,
        "has_test_split": True,
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
