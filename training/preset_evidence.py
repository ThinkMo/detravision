from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path

from training.contracts import atomic_write_json

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _probability(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("metric must be numeric")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("metric must be finite and between zero and one")
    return float(value)


def _list(value: object) -> list:
    if hasattr(value, "tolist"):
        value = value.tolist()
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else []


def validation_operating_points(metrics: object) -> dict[str, object]:
    """Save a bounded confidence grid at IoU 0.5, aligned by observed class ID."""
    curves = {}
    for curve in _list(getattr(metrics, "curves_results", None)):
        parts = _list(curve)
        if len(parts) == 4 and str(parts[2]).casefold() == "confidence":
            key = str(parts[3]).casefold().split("-")[0].split("(")[0]
            if key in {"precision", "recall"}:
                curves[key] = (_list(parts[0]), _list(parts[1]))
    if set(curves) != {"precision", "recall"}:
        return {}
    grid, precision = curves["precision"]
    recall_grid, recall = curves["recall"]
    if not grid or grid != recall_grid:
        return {}
    names = getattr(metrics, "names", {})
    ids = getattr(getattr(metrics, "box", None), "ap_class_index", None)
    class_ids = _list(ids) if ids is not None else sorted(names) if isinstance(names, Mapping) else list(range(len(names)))
    indices = sorted({round(i * (len(grid) - 1) / min(200, len(grid) - 1)) for i in range(min(201, len(grid)))}) if len(grid) > 1 else [0]
    result = {}
    for row, class_id in enumerate(class_ids):
        if row >= len(precision) or row >= len(recall):
            continue
        p, r = _list(precision[row]), _list(recall[row])
        if len(p) != len(grid) or len(r) != len(grid):
            continue
        try:
            values = {"confidence": [round(_probability(grid[i]), 6) for i in indices],
                      "precision": [round(_probability(p[i]), 6) for i in indices],
                      "recall": [round(_probability(r[i]), 6) for i in indices]}
            if values["confidence"] != sorted(values["confidence"]):
                continue
            result[str(names[int(class_id)])] = values
        except (TypeError, ValueError, KeyError, IndexError):
            continue
    return result


def _read(path: Path) -> dict:
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("evidence artifact exceeds one MiB")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("evidence artifact must be an object")
    return payload


def _recall_at_precision(curve: object, floor: float) -> float | None:
    if not isinstance(curve, dict):
        return None
    p, r, confidence = (curve.get(k, []) for k in ("precision", "recall", "confidence"))
    if not isinstance(p, list) or not isinstance(r, list) or not isinstance(confidence, list) or not p or len(p) != len(r) or len(p) != len(confidence):
        return None
    points = [(_probability(pv), _probability(rv), _probability(cv))
              for pv, rv, cv in zip(p, r, confidence)]
    candidates = [rv for pv, rv, _cv in points if pv >= floor]
    return max(candidates) if candidates else None


def _stats(values: list[float | None], baseline: list[float | None]) -> dict:
    if any(v is None for v in values) or any(v is None for v in baseline):
        return {"mean": None, "std": None, "paired_delta_mean": None}
    return {"mean": statistics.mean(values),
            "std": statistics.stdev(values) if len(values) > 1 else None,
            "paired_delta_mean": statistics.mean(v - b for v, b in zip(values, baseline))}


def compare_runs(run_dirs: Sequence[Path], *, precision_floor: float = 0.85) -> dict:
    """Compare saved best-checkpoint validation without training or loading any model."""
    floor = _probability(precision_floor)
    if len(run_dirs) < 2:
        raise ValueError("at least two runs are required")
    groups: dict[str, dict[int, dict]] = {}
    identity = None
    profile_configs = {}
    classes = []
    for directory in run_dirs:
        config = _read(Path(directory) / "effective_config.json")
        result = _read(Path(directory) / "validation.json")
        provenance = config.get("provenance") or {}
        runtime = config.get("runtime") or {}
        if result.get("split") != "val" or not runtime or config.get("resumed") is not False:
            raise ValueError("fresh initialized runs with validation evidence are required")
        digest, weights = provenance.get("dataset_manifest_digest"), config.get("base_model_sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest) or not isinstance(weights, str) or not _SHA256.fullmatch(weights):
            raise ValueError("dataset and initial weight digests are required")
        profile = provenance.get("training_profile")
        if profile not in {"balanced", "small_object", "high_recall", "stable_finetune", "custom"}:
            raise ValueError("unknown profile")
        seed = config.get("advanced_config", {}).get("seed")
        if type(seed) is not int or seed < 0:
            raise ValueError("a non-negative seed is required")
        base = config.get("base", {})
        signature = {"dataset_manifest_digest": digest, "base_model_sha256": weights,
                     "base_model": base.get("base_model"), "epochs": base.get("epochs"),
                     "patience": base.get("patience"), "requested_batch": base.get("batch"),
                     "actual_batch": runtime.get("batch"), "device": runtime.get("device"),
                     "versions": runtime.get("versions"), "profile_version": provenance.get("profile_version"),
                     "dataset_classes": config.get("dataset_classes")}
        if any(value is None for value in signature.values()):
            raise ValueError("complete comparison provenance is required")
        if identity is None:
            identity, classes = signature, signature["dataset_classes"]
        elif signature != identity:
            raise ValueError("runs must use the same dataset, initial weights, budget, batch, environment and profile version")
        if not isinstance(classes, list) or not classes or len(set(classes)) != len(classes):
            raise ValueError("dataset classes are required")
        settings = {"imgsz": base.get("imgsz"),
                    "advanced_config": {k: v for k, v in config.get("advanced_config", {}).items() if k != "seed"}}
        if profile in profile_configs and settings != profile_configs[profile]:
            raise ValueError("a profile must keep identical configuration across seeds")
        profile_configs[profile] = settings
        score = _probability(result.get("metrics", {}).get("map50_95"))
        per_class = {}
        for name in classes:
            raw = result.get("metrics", {}).get("classes", {}).get(name, {}).get("map50_95")
            per_class[name] = {"map50_95": _probability(raw) if raw is not None else None,
                               "recall": _recall_at_precision(result.get("precision_recall", {}).get(name), floor)}
        if seed in groups.setdefault(profile, {}):
            raise ValueError("duplicate profile/seed run")
        groups[profile][seed] = {"map50_95": score, "classes": per_class, "imgsz": base.get("imgsz")}
    if "balanced" not in groups or len(groups) < 2:
        raise ValueError("balanced and at least one candidate profile are required")
    seeds = sorted(groups["balanced"])
    if any(sorted(rows) != seeds for rows in groups.values()):
        raise ValueError("every profile must have the same paired seeds")
    profiles = {}
    for profile, rows in sorted(groups.items()):
        reference = groups["balanced"]
        profiles[profile] = {
            "paired_seeds": seeds, "input_sizes": sorted({rows[s]["imgsz"] for s in seeds}),
            "map50_95": _stats([rows[s]["map50_95"] for s in seeds], [reference[s]["map50_95"] for s in seeds]),
            "classes": {name: {
                "map50_95": _stats([rows[s]["classes"][name]["map50_95"] for s in seeds], [reference[s]["classes"][name]["map50_95"] for s in seeds]),
                "recall_at_precision": _stats([rows[s]["classes"][name]["recall"] for s in seeds], [reference[s]["classes"][name]["recall"] for s in seeds]),
            } for name in classes},
        }
    return {"schema_version": 1, "split": "val", "precision_floor": floor,
            "evidence_status": "collected" if len(seeds) >= 3 else "insufficient_seeds",
            "provenance": identity, "profiles": profiles,
            "interpretation": "Paired validation measurements; not a guarantee of improvement. Recall uses the saved confidence grid at IoU 0.5. Missing operating points remain null."}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare saved preset validation evidence; never starts training")
    parser.add_argument("runs", nargs="+", type=Path, help="run directories with effective_config.json and validation.json")
    parser.add_argument("--precision-floor", type=float, default=0.85)
    parser.add_argument("--out", required=True, type=Path, help="new JSON report file")
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error("output already exists")
    try:
        report = compare_runs(args.runs, precision_floor=args.precision_floor)
        atomic_write_json(args.out, report)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
