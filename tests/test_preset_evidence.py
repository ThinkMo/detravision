import json
from types import SimpleNamespace

import pytest


def record(tmp_path, profile, seed, score, *, digest="d" * 64):
    directory = tmp_path / f"{profile}-{seed}"
    directory.mkdir()
    config = {
        "provenance": {"dataset_manifest_digest": digest, "training_profile": profile, "profile_version": 2},
        "base": {"base_model": "yolo11n.pt", "epochs": 100, "imgsz": 640, "batch": "4", "patience": 50},
        "base_model_sha256": "a" * 64, "dataset_classes": ["fire"], "resumed": False,
        "advanced_config": {"seed": seed},
        "runtime": {"versions": {"ultralytics": "8.4.120", "torch": "2.8.0", "cuda": None}, "device": "cpu", "batch": 4},
    }
    validation = {
        "split": "val", "best_sha256": "b" * 64,
        "metrics": {"map50_95": score},
        "precision_recall": {"fire": {"confidence": [0.1, 0.5, 0.9], "precision": [0.5, 0.86, 0.95], "recall": [0.95, 0.8, 0.4]}},
    }
    (directory / "effective_config.json").write_text(json.dumps(config), encoding="utf-8")
    (directory / "validation.json").write_text(json.dumps(validation), encoding="utf-8")
    return directory


def test_report_pairs_seeds_and_uses_validation_recall_at_a_precision_floor(tmp_path):
    """Catches unpaired runs and best-F1 recall being presented as fixed-precision recall."""
    from training.preset_evidence import compare_runs
    runs = [record(tmp_path, "balanced", 0, 0.4), record(tmp_path, "high_recall", 0, 0.5),
            record(tmp_path, "balanced", 1, 0.6), record(tmp_path, "high_recall", 1, 0.6)]
    report = compare_runs(runs, precision_floor=0.85)
    row = report["profiles"]["high_recall"]
    assert row["paired_seeds"] == [0, 1]
    assert row["map50_95"]["mean"] == pytest.approx(0.55)
    assert row["map50_95"]["paired_delta_mean"] == pytest.approx(0.05)
    assert row["classes"]["fire"]["recall_at_precision"]["mean"] == 0.8
    assert report["evidence_status"] == "insufficient_seeds"
    assert report["split"] == "val"
    assert str(tmp_path) not in json.dumps(report)


@pytest.mark.parametrize("change", ["digest", "test", "weights", "duplicate", "unpaired", "budget", "runtime", "resumed"])
def test_report_rejects_incomparable_or_missing_evidence(tmp_path, change):
    """Catches accepting different datasets, budgets, starts or test-set evidence."""
    from training.preset_evidence import compare_runs
    left = record(tmp_path, "balanced", 0, 0.4)
    right = record(tmp_path, "high_recall", 0, 0.5)
    path = right / ("validation.json" if change == "test" else "effective_config.json")
    payload = json.loads(path.read_text("utf-8"))
    if change == "digest": payload["provenance"]["dataset_manifest_digest"] = "e" * 64
    elif change == "test": payload["split"] = "test"
    elif change == "weights": payload["base_model_sha256"] = "c" * 64
    elif change == "duplicate": payload["provenance"]["training_profile"] = "balanced"
    elif change == "unpaired": payload["advanced_config"]["seed"] = 1
    elif change == "budget": payload["base"]["epochs"] = 150
    elif change == "runtime": payload["runtime"] = None
    elif change == "resumed": payload["resumed"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        compare_runs([left, right], precision_floor=0.85)


def test_confidence_curves_use_observed_sparse_class_ids_and_same_threshold_grid():
    """Catches missing-class curve rows being attributed to the wrong class."""
    from training.preset_evidence import validation_operating_points
    metrics = SimpleNamespace(
        names={0: "absent", 2: "fire"}, box=SimpleNamespace(ap_class_index=[2]),
        curves_results=[
            ([0.1, 0.5], [[0.5, 0.9]], "Confidence", "Precision"),
            ([0.1, 0.5], [[0.9, 0.6]], "Confidence", "Recall"),
        ],
    )
    assert validation_operating_points(metrics) == {
        "fire": {"confidence": [0.1, 0.5], "precision": [0.5, 0.9], "recall": [0.9, 0.6]},
    }
    metrics.curves_results[1] = ([0.2, 0.5], [[0.9, 0.6]], "Confidence", "Recall")
    assert validation_operating_points(metrics) == {}


def test_same_profile_must_keep_configuration_and_size_constant_across_seeds(tmp_path):
    """Catches different experiments being aggregated under one preset name."""
    from training.preset_evidence import compare_runs
    runs = [record(tmp_path, "balanced", 0, 0.4), record(tmp_path, "high_recall", 0, 0.5),
            record(tmp_path, "balanced", 1, 0.4), record(tmp_path, "high_recall", 1, 0.5)]
    path = runs[-1] / "effective_config.json"
    config = json.loads(path.read_text("utf-8"))
    config["advanced_config"]["lr0"] = 0.01
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError):
        compare_runs(runs)
