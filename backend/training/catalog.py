from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from backend.core.config import Settings
from training.configuration import (
    catalog_schema,
    resolve_training_config,
)


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
    dataset_key: str
    base_model: str
    imgsz: int
    epochs: int
    batch: str
    device: str
    patience: int
    executor: str = "local"
    mode: str = "basic"
    profile: str = "balanced"
    advanced_config: Mapping[str, object] | None = None
    acceptance_config: Mapping[str, object] | None = None
    acceptance_template: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.dataset_key, str)
            or not self.dataset_key.startswith("dataset:")
            or not self.dataset_key.removeprefix("dataset:").isdigit()
            or int(self.dataset_key.removeprefix("dataset:")) < 1
        ):
            raise ValueError("unsupported dataset")
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
        advanced = resolve_training_config(
            mode=self.mode,
            profile=self.profile,
            overrides=self.advanced_config,
            epochs=self.epochs,
        )
        if self.acceptance_config is not None and not isinstance(
            self.acceptance_config, Mapping
        ):
            raise ValueError("acceptance config must be an object")
        if self.acceptance_template not in {None, "generic", "safety_helmet"}:
            raise ValueError("unsupported acceptance template")
        object.__setattr__(self, "mode", "basic" if self.mode == "basic" else "advanced")
        object.__setattr__(self, "profile", "balanced" if self.mode == "basic" else self.profile)
        object.__setattr__(self, "advanced_config", advanced)
        object.__setattr__(
            self,
            "acceptance_config",
            dict(self.acceptance_config) if self.acceptance_config is not None else None,
        )


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


def _model_available(settings: Settings, model_id: str) -> bool:
    roots = (
        Path(__file__).resolve().parents[2],
        settings.training_model_cache_dir.resolve(),
    )
    for root in roots:
        try:
            candidate = (root / model_id).resolve()
            candidate.relative_to(root)
            if candidate.is_file():
                return True
        except (OSError, ValueError):
            continue
    return False


def training_catalog(settings: Settings, dataset_service=None) -> dict[str, Any]:
    datasets = dataset_service.catalog() if dataset_service is not None else []
    available_datasets = [item for item in datasets if item.get("available")]
    return {
        **catalog_schema(),
        "models": [
            {
                "id": model_id,
                "series": model_id[4:6],
                "size": model_id[6],
                "available": _model_available(settings, model_id),
            }
            for model_id in MODEL_IDS
        ],
        "executors": [
            {"id": "local", "enabled": settings.training_enabled},
            {"id": "remote", "enabled": False},
        ],
        "dataset": _dataset_summary(settings),
        "datasets": datasets,
        "default_dataset_key": (
            available_datasets[0]["key"] if available_datasets else None
        ),
    }
