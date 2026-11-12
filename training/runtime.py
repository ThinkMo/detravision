from __future__ import annotations

import math
import platform
import sys
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from training.configuration import catalog_schema
from training.contracts import atomic_write_json


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _param_groups(optimizer: object) -> list[dict[str, Any]]:
    result = []
    for group in getattr(optimizer, "param_groups", []):
        row: dict[str, Any] = {"parameter_count": len(group.get("params", []))}
        for key in ("lr", "initial_lr", "momentum", "weight_decay", "eps"):
            value = _number(group.get(key))
            if value is not None:
                row[key] = value
        betas = group.get("betas")
        if isinstance(betas, (list, tuple)) and len(betas) == 2:
            row["betas"] = [_number(value) for value in betas]
        if group.get("param_group") in {"bias", "weight", "bn", "muon"}:
            row["kind"] = group["param_group"]
        if type(group.get("use_muon")) is bool:
            row["use_muon"] = group["use_muon"]
        result.append(row)
    return result


def _versions() -> dict[str, str | None]:
    result: dict[str, str | None] = {"python": platform.python_version()}
    for name in ("ultralytics", "torch"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    torch = sys.modules.get("torch")
    result["cuda"] = getattr(getattr(torch, "version", None), "cuda", None)
    return result


def _safe_args(trainer: object) -> dict[str, object]:
    args = getattr(trainer, "args", None)
    raw = args if isinstance(args, Mapping) else vars(args) if args is not None else {}
    fields = catalog_schema()["advanced_config_schema"]["fields"]
    result: dict[str, object] = {}
    for key, descriptor in fields.items():
        value = raw.get(key)
        if descriptor["type"] in {"number", "integer"}:
            numeric = _number(value)
            if numeric is not None:
                result[key] = numeric
        elif descriptor["type"] == "boolean" and type(value) is bool:
            result[key] = value
        elif key == "cache" and type(value) is bool:
            result[key] = "ram" if value else "false"
        elif value in descriptor.get("choices", ()):
            result[key] = value
    return result


class RuntimeRecorder:
    """Persist requested configuration separately from observed trainer state.

    Only allowlisted scalar fields are exported; trainer args contain private paths.
    No runtime values are guessed before the optimizer has been initialized.
    """

    def __init__(self, path: Path | None, requested: dict[str, object]):
        self.path = path
        self.payload = {**requested, "artifact_schema_version": 2, "runtime": None}

    def write(self) -> None:
        if self.path is not None:
            atomic_write_json(self.path, self.payload)

    def capture(self, trainer: object) -> None:
        if self.path is None:
            self.path = Path(getattr(trainer, "save_dir")) / "effective_config.json"
        optimizer = getattr(trainer, "optimizer", None)
        if optimizer is None:
            self.write()
            return
        groups = _param_groups(optimizer)
        runtime = self.payload["runtime"]
        if runtime is None:
            advanced = _safe_args(trainer)
            name = type(optimizer).__name__
            advanced["optimizer"] = name
            amp = getattr(trainer, "amp", None)
            if amp is not None:
                advanced["amp"] = bool(amp)
            initial_lrs = [g.get("initial_lr", g.get("lr")) for g in groups]
            initial_lrs = [lr for lr in initial_lrs if lr is not None]
            if initial_lrs:
                advanced["lr0"] = min(initial_lrs)
            if groups:
                group = groups[0]
                momentum = group.get("betas", [group.get("momentum")])[0]
                if momentum is not None:
                    advanced["momentum"] = momentum
            runtime = {
                "advanced_config": advanced,
                "optimizer": {"name": name, "param_groups": groups},
                "versions": _versions(),
            }
            self.payload["runtime"] = runtime
        runtime["optimizer"]["param_groups"] = groups
        batch = getattr(trainer, "batch_size", None)
        runtime["batch"] = batch if type(batch) is int and batch > 0 else None
        device = str(getattr(trainer, "device", ""))
        runtime["device"] = device if device in {"cpu", "mps"} or (device.startswith("cuda:") and device[5:].isdigit()) else None
        epoch = getattr(trainer, "epoch", None)
        runtime["epoch"] = epoch + 1 if type(epoch) is int else None
        self.write()

    def attach(self, model: object) -> None:
        for event in ("on_train_start", "on_fit_epoch_end", "on_train_end"):
            model.add_callback(event, self.capture)
