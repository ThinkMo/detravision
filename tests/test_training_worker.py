from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from training.contracts import (
    TrainingManifest,
    TrainingProgress,
    TrainingResult,
    atomic_write_json,
    extract_metrics,
    meets_acceptance,
)
from training.configuration import (
    evaluate_acceptance,
    normalize_acceptance_config,
    resolve_training_config,
)
from training.worker import main


def metric_fixture(
    *,
    map50: float = 0.85,
    no_helmet_precision: float = 0.85,
    no_helmet_recall: float = 0.90,
):
    return SimpleNamespace(
        names={0: "person", 1: "helmet", 2: "no_helmet"},
        curves_results=[
            ([0.0, 0.5, 0.9], [[0.1, 0.8, 0.6], [0.2, 0.7, 0.5], [0.1, 0.6, 0.9]], "Confidence", "F1-Confidence(B)"),
        ],
        confusion_matrix=SimpleNamespace(
            matrix=[[8, 1, 0, 2], [1, 9, 0, 1], [0, 0, 7, 3], [2, 1, 4, 0]]
        ),
        box=SimpleNamespace(
            mp=0.87,
            mr=0.91,
            map50=map50,
            map=0.61,
            ap_class_index=[0, 1, 2],
            p=[0.91, 0.92, no_helmet_precision],
            r=[0.88, 0.93, no_helmet_recall],
            ap50=[0.86, 0.94, 0.89],
            ap=[0.55, 0.72, 0.57],
        ),
    )


def manifest_payload(tmp_path: Path, **overrides: object) -> dict[str, object]:
    dataset = tmp_path / "private" / "dataset.yaml"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text(
        "train: images/train\nval: images/val\ntest: images/test\n"
        "names: [person, helmet, no_helmet]\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "runs" / "run-0123456789abcdef0123456789abcdef"
    run_dir.mkdir(parents=True, exist_ok=True)
    values: dict[str, object] = {
        "base_model": "yolo11m.pt",
        "dataset_yaml": str(dataset.resolve()),
        "run_dir": str(run_dir.resolve()),
        "model_cache_dir": str((tmp_path / "models").resolve()),
        "epochs": 100,
        "imgsz": 640,
        "batch": "auto",
        "device": "auto",
        "patience": 50,
    }
    values.update(overrides)
    return values


def advanced_manifest_payload(
    tmp_path: Path,
    *,
    classes: list[str] | None = None,
    acceptance_config: dict[str, object] | None = None,
    **overrides: object,
) -> dict[str, object]:
    payload = manifest_payload(tmp_path)
    selected_classes = classes or ["person", "helmet", "no_helmet"]
    payload.update(
        config_schema_version=1,
        dataset_classes=selected_classes,
        advanced_config=resolve_training_config(
            mode="advanced",
            profile="high_recall",
            overrides={"lr0": 0.002, "workers": 4},
            epochs=int(payload["epochs"]),
        ),
        acceptance_config=(
            acceptance_config
            or normalize_acceptance_config(None, classes=selected_classes)
        ),
    )
    payload.update(overrides)
    return payload


def test_contracts_validate_json_types_and_round_trip(tmp_path: Path):
    """Catches malformed manager manifests reaching the heavy YOLO boundary."""
    payload = manifest_payload(tmp_path)

    manifest = TrainingManifest.from_json(payload)

    assert manifest.to_json() == payload
    progress = TrainingProgress.from_json(
        {"current_epoch": 4, "total_epochs": 100, "metrics": {"map50": 0.4}}
    )
    assert progress.to_json() == {
        "current_epoch": 4,
        "total_epochs": 100,
        "metrics": {"map50": 0.4},
    }
    result = TrainingResult.from_json(
        {
            "status": "completed",
            "metrics": extract_metrics(metric_fixture()),
            "accepted": True,
            "best_sha256": "a" * 64,
            "error_code": None,
            "error_message": None,
        }
    )
    assert result.status == "completed"


@pytest.mark.parametrize("resume_value", ["relative/last.pt", "outside"])
def test_manifest_requires_resume_checkpoint_to_be_absolute_and_inside_run(
    tmp_path: Path, resume_value: str
):
    """Catches a resume attempt loading an arbitrary checkpoint outside its run."""
    payload = manifest_payload(tmp_path)
    if resume_value == "outside":
        checkpoint = tmp_path / "outside" / "last.pt"
        checkpoint.parent.mkdir()
        checkpoint.write_bytes(b"outside")
        payload["resume_checkpoint"] = str(checkpoint.resolve())
    else:
        payload["resume_checkpoint"] = resume_value

    with pytest.raises(ValueError):
        TrainingManifest.from_json(payload)


def test_manifest_accepts_only_a_contained_resume_checkpoint(tmp_path: Path):
    """Catches the trusted resume checkpoint being omitted from the worker contract."""
    payload = manifest_payload(tmp_path)
    checkpoint = Path(str(payload["run_dir"])) / "resume" / "last.pt"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"resume")
    payload["resume_checkpoint"] = str(checkpoint.resolve())

    manifest = TrainingManifest.from_json(payload)

    assert manifest.resume_checkpoint == str(checkpoint.resolve())
    assert manifest.to_json() == payload


def test_advanced_manifest_round_trips_complete_versioned_snapshots(tmp_path: Path):
    """Catches worker manifests dropping reproducible config or arbitrary classes."""
    payload = advanced_manifest_payload(tmp_path, classes=["fire", "smoke"])

    manifest = TrainingManifest.from_json(payload)

    assert manifest.to_json() == payload
    assert manifest.dataset_classes == ("fire", "smoke")
    assert manifest.advanced_config["optimizer"] == "AdamW"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("config_schema_version", 2),
        ("advanced_config", {"optimizer": "AdamW", "command": "format C:"}),
    ],
)
def test_advanced_manifest_rejects_unknown_schema_and_partial_or_arbitrary_config(
    tmp_path: Path, field: str, value: object
):
    """Catches unversioned or client-controlled keys crossing into YOLO.train."""
    payload = advanced_manifest_payload(tmp_path)
    payload[field] = value

    with pytest.raises(ValueError):
        TrainingManifest.from_json(payload)


@pytest.mark.parametrize("contract", ["manifest", "progress", "result"])
def test_contracts_reject_extra_fields(tmp_path: Path, contract: str):
    """Catches injected commands or unversioned fields crossing JSON boundaries."""
    if contract == "manifest":
        payload = manifest_payload(tmp_path, command="format C:")
        loader = TrainingManifest.from_json
    elif contract == "progress":
        payload = {
            "current_epoch": 1,
            "total_epochs": 2,
            "metrics": {},
            "command": "format C:",
        }
        loader = TrainingProgress.from_json
    else:
        payload = {
            "status": "failed",
            "metrics": None,
            "accepted": False,
            "best_sha256": None,
            "error_code": "training_failed",
            "error_message": "Training did not produce a valid model.",
            "command": "format C:",
        }
        loader = TrainingResult.from_json

    with pytest.raises(ValueError):
        loader(payload)


def valid_completed_result() -> dict[str, object]:
    return {
        "status": "completed",
        "metrics": extract_metrics(metric_fixture()),
        "accepted": True,
        "best_sha256": "a" * 64,
        "error_code": None,
        "error_message": None,
    }


def test_generic_result_validates_acceptance_checks_against_snapshot():
    """Catches forged worker acceptance or failure explanations."""
    metrics = {
        "precision": 0.72,
        "recall": 0.77,
        "map50": 0.8,
        "map50_95": 0.55,
        "classes": {
            "fire": {
                "precision": 0.79,
                "recall": 0.81,
                "map50": 0.83,
                "map50_95": 0.58,
            }
        },
    }
    config = normalize_acceptance_config(
        {"overall": {"map50": 0.75}, "classes": {"fire": {"recall": 0.8}}},
        classes=["fire"],
    )
    accepted, checks = evaluate_acceptance(metrics, config)
    payload = {
        "status": "completed",
        "metrics": metrics,
        "accepted": accepted,
        "acceptance_checks": checks,
        "best_sha256": "a" * 64,
        "error_code": None,
        "error_message": None,
    }

    result = TrainingResult.from_json(payload, acceptance_config=config)

    assert result.acceptance_checks == checks
    forged = {**payload, "acceptance_checks": [{**checks[0], "passed": False}] + checks[1:]}
    with pytest.raises(ValueError):
        TrainingResult.from_json(forged, acceptance_config=config)


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        (("metrics", "map50"), "0.85"),
        (("metrics", "map50"), float("nan")),
        (("metrics", "map50_95"), float("inf")),
        (("metrics", "classes"), []),
        (("metrics", "classes", "no_helmet", "precision"), Path("secret.pt")),
    ],
)
def test_result_rejects_non_json_safe_or_malformed_metrics(
    mutation: tuple[str, ...], value: object
):
    """Catches malformed worker metrics being persisted or exposed by the manager."""
    payload = valid_completed_result()
    target = payload
    for key in mutation[:-1]:
        target = target[key]  # type: ignore[index,assignment]
    target[mutation[-1]] = value  # type: ignore[index]

    with pytest.raises(ValueError):
        TrainingResult.from_json(payload)


def test_result_rejects_missing_or_extra_metric_keys():
    """Catches incomplete or silently extended metric schemas reaching persistence."""
    for changed in ("missing", "extra", "class_missing", "class_extra"):
        payload = valid_completed_result()
        metrics = payload["metrics"]
        if changed == "missing":
            del metrics["recall"]
        elif changed == "extra":
            metrics["accuracy"] = 0.99
        elif changed == "class_missing":
            del metrics["classes"]["no_helmet"]["recall"]
        else:
            metrics["classes"]["no_helmet"]["accuracy"] = 0.99
        with pytest.raises(ValueError):
            TrainingResult.from_json(payload)


def test_result_rejects_acceptance_inconsistent_with_metrics():
    """Catches a forged acceptance flag overriding the documented policy."""
    payload = valid_completed_result()
    payload["accepted"] = False

    with pytest.raises(ValueError):
        TrainingResult.from_json(payload)


@pytest.mark.parametrize(
    ("metric_path", "value"),
    [
        (("metrics", "precision"), 999.0),
        (("metrics", "recall"), -0.1),
        (("metrics", "classes", "no_helmet", "map50"), 999.0),
        (("metrics", "classes", "helmet", "recall"), -0.1),
    ],
)
def test_result_rejects_metrics_outside_probability_range(
    metric_path: tuple[str, ...], value: float
):
    """Catches corrupt overall or per-class ratios being accepted as metrics."""
    payload = valid_completed_result()
    target = payload
    for key in metric_path[:-1]:
        target = target[key]  # type: ignore[index,assignment]
    target[metric_path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValueError):
        TrainingResult.from_json(payload)


@pytest.mark.parametrize(
    ("error_code", "error_message"),
    [
        ("arbitrary", "Training failed."),
        ("training_failed", "x" * 501),
        ("training_failed", "failed\nD:\\private\\best.pt"),
        ("training_failed", "failed at /srv/private/best.pt"),
    ],
)
def test_failed_result_rejects_unstable_or_unsafe_errors(
    error_code: str, error_message: str
):
    """Catches arbitrary diagnostics and paths entering result.error fields."""
    with pytest.raises(ValueError):
        TrainingResult.from_json(
            {
                "status": "failed",
                "metrics": None,
                "accepted": False,
                "best_sha256": None,
                "error_code": error_code,
                "error_message": error_message,
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("epochs", True),
        ("imgsz", "640"),
        ("batch", -1),
        ("device", 0),
        ("dataset_yaml", "relative.yaml"),
        ("run_dir", "relative-run"),
        ("base_model", "../../private.pt"),
    ],
)
def test_manifest_rejects_ambiguous_or_untrusted_values(
    tmp_path: Path, field: str, value: object
):
    """Catches unsafe path/model/type values bypassing manager validation."""
    with pytest.raises(ValueError):
        TrainingManifest.from_json(manifest_payload(tmp_path, **{field: value}))


def test_extract_metrics_preserves_overall_and_per_class_values():
    """Catches no_helmet acceptance being computed from another class or aggregate."""
    extracted = extract_metrics(metric_fixture())

    assert extracted == {
        "precision": 0.87,
        "recall": 0.91,
        "map50": 0.85,
        "map50_95": 0.61,
        "classes": {
            "person": {
                "precision": 0.91,
                "recall": 0.88,
                "map50": 0.86,
                "map50_95": 0.55,
            },
            "helmet": {
                "precision": 0.92,
                "recall": 0.93,
                "map50": 0.94,
                "map50_95": 0.72,
            },
            "no_helmet": {
                "precision": 0.85,
                "recall": 0.90,
                "map50": 0.89,
                "map50_95": 0.57,
            },
        },
    }


def test_extract_metrics_uses_none_when_per_class_precision_is_unavailable():
    """Catches invented per-class precision/recall making a run appear accepted."""
    metrics = metric_fixture()
    del metrics.box.p
    del metrics.box.r

    extracted = extract_metrics(metrics)

    assert extracted["classes"]["no_helmet"]["precision"] is None
    assert extracted["classes"]["no_helmet"]["recall"] is None
    assert meets_acceptance(extracted) is False


def test_extract_metrics_uses_per_class_maps_for_ultralytics_ap_rows():
    """Catches real multi-IoU AP rows being converted directly to a scalar."""
    metrics = metric_fixture()
    metrics.box.ap = [[0.86, 0.55], [0.94, 0.72], [0.89, 0.57]]
    metrics.box.maps = [0.55, 0.72, 0.57]

    extracted = extract_metrics(metrics)

    assert extracted["classes"]["person"]["map50_95"] == 0.55
    assert extracted["classes"]["no_helmet"]["map50_95"] == 0.57


def test_extract_metrics_normalizes_library_numeric_scalars_to_json_floats():
    """Catches non-builtin numeric scalars from Ultralytics breaking extraction."""
    metrics = metric_fixture()
    metrics.box.mp = Decimal("0.87")
    metrics.box.p = [Decimal("0.91"), Decimal("0.92"), Decimal("0.85")]

    extracted = extract_metrics(metrics)

    assert extracted["precision"] == 0.87
    assert type(extracted["precision"]) is float
    assert extracted["classes"]["no_helmet"]["precision"] == 0.85


@pytest.mark.parametrize(
    ("map50", "precision", "recall", "accepted"),
    [
        (0.85, 0.85, 0.90, True),
        (0.849999, 0.85, 0.90, False),
        (0.85, 0.849999, 0.90, False),
        (0.85, 0.85, 0.899999, False),
    ],
)
def test_acceptance_uses_exact_documented_boundaries(
    map50: float, precision: float, recall: float, accepted: bool
):
    """Catches strict-greater or rounded comparisons at customer thresholds."""
    extracted = extract_metrics(
        metric_fixture(
            map50=map50,
            no_helmet_precision=precision,
            no_helmet_recall=recall,
        )
    )
    assert meets_acceptance(extracted) is accepted


def test_atomic_json_replaces_content_without_leaving_temporary_files(tmp_path: Path):
    """Catches readers observing partial JSON or abandoned replacement files."""
    target = tmp_path / "progress.json"
    atomic_write_json(target, {"current_epoch": 1})
    atomic_write_json(target, {"current_epoch": 2, "label": "安全帽"})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "current_epoch": 2,
        "label": "安全帽",
    }
    assert [path for path in tmp_path.iterdir() if path != target] == []


class FakeYOLO:
    instances: list["FakeYOLO"] = []
    fail_stage: str | None = None

    def __init__(self, model: str):
        if self.fail_stage == "load":
            raise RuntimeError("D:\\private\\weights\\missing.pt cannot download")
        self.model = model
        self.cwd_at_load = Path.cwd()
        self.callbacks: dict[str, object] = {}
        self.train_calls: list[dict[str, object]] = []
        self.val_calls: list[dict[str, object]] = []
        self.trainer = SimpleNamespace(
            epoch=0,
            metrics={"metrics/mAP50(B)": 0.25},
            batch_size=6,
        )
        self.__class__.instances.append(self)

    def add_callback(self, name: str, callback):
        if self.fail_stage == "callback":
            raise OSError(1455, "D:\\private\\cufft64_11.dll")
        self.callbacks[name] = callback

    def train(self, **kwargs):
        self.train_calls.append(kwargs)
        if self.fail_stage == "cuda":
            raise RuntimeError("CUDA out of memory at D:\\private\\image.jpg")
        if self.fail_stage == "dataset":
            raise RuntimeError("Dataset D:\\private\\dataset.yaml images not found")
        if self.fail_stage == "train":
            raise RuntimeError("secret failure at D:\\private\\customer")
        self.trainer.epoch = 3
        self.callbacks["on_fit_epoch_end"](self.trainer)
        if self.fail_stage == "missing_best":
            return SimpleNamespace(save_dir=Path(str(kwargs["project"])) / "train")
        weights = Path(str(kwargs["project"])) / str(kwargs["name"]) / "weights"
        weights.mkdir(parents=True)
        (weights / "best.pt").write_bytes(b"best checkpoint")
        (weights / "last.pt").write_bytes(b"last checkpoint")
        (weights.parent / "results.png").write_bytes(b"results curve")
        return SimpleNamespace(save_dir=weights.parent)

    def val(self, **kwargs):
        self.val_calls.append(kwargs)
        if self.fail_stage == "evaluation":
            raise RuntimeError("D:\\private\\test image failed")
        if self.fail_stage == "confusion_outside":
            save_dir = Path(str(kwargs["project"])).parent / "private-evaluation"
        else:
            save_dir = Path(str(kwargs["project"])) / str(kwargs["name"])
        save_dir.mkdir(parents=True, exist_ok=True)
        if self.fail_stage != "confusion_missing":
            (save_dir / "confusion_matrix.png").write_bytes(b"confusion matrix")
        for name in ("PR_curve.png", "F1_curve.png", "P_curve.png", "R_curve.png"):
            (save_dir / name).write_bytes(name.encode("ascii"))
        metrics = metric_fixture()
        if self.fail_stage != "no_metrics_save_dir":
            metrics.save_dir = save_dir
        return metrics


@pytest.fixture(autouse=True)
def reset_fake_yolo():
    FakeYOLO.instances.clear()
    FakeYOLO.fail_stage = None


def write_manifest(tmp_path: Path, **overrides: object) -> tuple[Path, dict[str, object]]:
    payload = manifest_payload(tmp_path, **overrides)
    path = Path(str(payload["run_dir"])) / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload


def test_worker_dispatches_training_progress_test_evaluation_and_digest(
    tmp_path: Path, monkeypatch
):
    """Catches validation split/arguments or canonical artifacts drifting."""
    manifest_path, payload = write_manifest(tmp_path)
    starting_cwd = Path.cwd()

    code = main(
        ["--manifest", str(manifest_path)],
        yolo_cls=FakeYOLO,
        cuda_available=lambda: True,
        project_root=tmp_path,
    )

    assert code == 0
    assert Path.cwd() == starting_cwd
    training_model, evaluation_model = FakeYOLO.instances
    assert training_model.model == "yolo11m.pt"
    assert training_model.cwd_at_load == Path(str(payload["model_cache_dir"]))
    assert training_model.train_calls == [
        {
            "data": payload["dataset_yaml"],
            "epochs": 100,
            "imgsz": 640,
            "batch": -1,
            "device": 0,
            "workers": 0,
            "project": payload["run_dir"],
            "name": "train",
            "exist_ok": True,
            "patience": 50,
        }
    ]
    canonical_best = Path(str(payload["run_dir"])) / "weights" / "best.pt"
    assert Path(evaluation_model.model) == canonical_best
    assert evaluation_model.val_calls == [
        {
            "data": payload["dataset_yaml"],
            "split": "test",
            "imgsz": 640,
            "batch": 6,
            "device": 0,
            "workers": 0,
            "project": payload["run_dir"],
            "name": "test",
            "exist_ok": True,
            "plots": True,
        }
    ]
    progress = json.loads(
        (Path(str(payload["run_dir"])) / "progress.json").read_text(encoding="utf-8")
    )
    assert progress == {
        "current_epoch": 4,
        "total_epochs": 100,
        "metrics": {"map50": 0.25},
    }
    result = json.loads(
        (Path(str(payload["run_dir"])) / "result.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "completed"
    assert result["accepted"] is True
    assert result["best_sha256"] == (
        "ccf38fe71fc3c3a7f107a84ccf1d27b4df5042e974cda28e621ef243dd69750d"
    )
    assert result["metrics"]["classes"]["no_helmet"]["recall"] == 0.90
    assert canonical_best.read_bytes() == b"best checkpoint"
    assert (canonical_best.parent / "last.pt").read_bytes() == b"last checkpoint"
    assert (
        Path(str(payload["run_dir"])) / "confusion_matrix.png"
    ).read_bytes() == b"confusion matrix"
    assert (Path(str(payload["run_dir"])) / "results.png").read_bytes() == b"results curve"
    analysis = json.loads((Path(str(payload["run_dir"])) / "analysis.json").read_text(encoding="utf-8"))
    assert analysis["classes"]["no_helmet"]["recommended_confidence"] == 0.9
    assert analysis["classes"]["no_helmet"]["false_negatives"] == 4
    for name in ("PR_curve.png", "F1_curve.png", "P_curve.png", "R_curve.png"):
        assert (Path(str(payload["run_dir"])) / name).is_file()
    assert os.environ["YOLO_CONFIG_DIR"] == str(tmp_path / "data")
    assert os.environ["MPLCONFIGDIR"] == str(tmp_path / "data" / "matplotlib")


def test_worker_maps_only_canonical_advanced_fields_to_yolo_train(tmp_path: Path):
    """Catches advanced keys being omitted or service-owned paths being overridden."""
    payload = advanced_manifest_payload(tmp_path)
    manifest_path = Path(str(payload["run_dir"])) / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert main(
        ["--manifest", str(manifest_path)],
        yolo_cls=FakeYOLO,
        cuda_available=lambda: True,
        project_root=tmp_path,
    ) == 0

    call = FakeYOLO.instances[0].train_calls[0]
    assert call["optimizer"] == "AdamW"
    assert call["lr0"] == 0.002
    assert call["mixup"] == 0.1
    assert call["workers"] == 4
    assert set(call) == {
        "data",
        "epochs",
        "imgsz",
        "batch",
        "device",
        "project",
        "name",
        "exist_ok",
        "patience",
        *payload["advanced_config"],
    }
    assert "run_dir" not in call
    assert "model_cache_dir" not in call
    assert call["project"] == payload["run_dir"]
    effective = json.loads(
        (Path(str(payload["run_dir"])) / "effective_config.json").read_text("utf-8")
    )
    assert effective == {
        "config_schema_version": 1,
        "base": {
            "base_model": "yolo11m.pt",
            "epochs": 100,
            "imgsz": 640,
            "batch": "auto",
            "device": "auto",
            "patience": 50,
        },
        "advanced_config": payload["advanced_config"],
    }
    serialized = json.dumps(effective)
    assert str(tmp_path) not in serialized
    for forbidden in ("data", "project", "name", "save_dir", "resume"):
        assert forbidden not in effective


class GenericFakeYOLO(FakeYOLO):
    def val(self, **kwargs):
        metrics = super().val(**kwargs)
        metrics.names = {2: "fire", 5: "smoke"}
        metrics.box.ap_class_index = [2, 5]
        metrics.box.p = [0.79, 0.74]
        metrics.box.r = [0.81, 0.76]
        metrics.box.ap50 = [0.83, 0.78]
        metrics.box.ap = [0.58, 0.52]
        metrics.box.maps = [0.0, 0.0, 0.58, 0.0, 0.0, 0.52]
        metrics.box.map50 = 0.8
        return metrics


def test_worker_emits_dynamic_arbitrary_classes_and_generic_acceptance(tmp_path: Path):
    """Catches hard-coded helmet metrics replacing arbitrary dataset classes."""
    config = normalize_acceptance_config(
        {"overall": {"map50": 0.75}, "classes": {"fire": {"recall": 0.8}}},
        classes=["fire", "smoke"],
    )
    payload = advanced_manifest_payload(
        tmp_path, classes=["fire", "smoke"], acceptance_config=config
    )
    manifest_path = Path(str(payload["run_dir"])) / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert main(
        ["--manifest", str(manifest_path)],
        yolo_cls=GenericFakeYOLO,
        cuda_available=lambda: False,
        project_root=tmp_path,
    ) == 0

    result = json.loads(
        (Path(str(payload["run_dir"])) / "result.json").read_text("utf-8")
    )
    assert result["accepted"] is True
    assert result["metrics"]["classes"]["fire"]["recall"] == 0.81
    assert result["acceptance_checks"][0]["metric"] == "map50"
    assert result["acceptance_checks"][-1]["class_name"] == "fire"


def test_worker_resolves_auto_device_to_cpu_and_numeric_batch(tmp_path: Path):
    """Catches worker forwarding UI string choices directly to Ultralytics."""
    manifest_path, _ = write_manifest(tmp_path, batch="4")

    assert main(
        ["--manifest", str(manifest_path)],
        yolo_cls=FakeYOLO,
        cuda_available=lambda: False,
        project_root=tmp_path,
    ) == 0

    call = FakeYOLO.instances[0].train_calls[0]
    assert call["device"] == "cpu"
    assert call["batch"] == 4


def test_worker_resumes_from_contained_checkpoint_instead_of_base_model(tmp_path: Path):
    """Catches a resume request silently starting a fresh base-model training run."""
    payload = manifest_payload(tmp_path)
    checkpoint = Path(str(payload["run_dir"])) / "resume" / "last.pt"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"resume checkpoint")
    manifest_path, _ = write_manifest(
        tmp_path, resume_checkpoint=str(checkpoint.resolve())
    )

    assert main(
        ["--manifest", str(manifest_path)],
        yolo_cls=FakeYOLO,
        cuda_available=lambda: False,
        project_root=tmp_path,
    ) == 0

    training_model = FakeYOLO.instances[0]
    assert Path(training_model.model) == checkpoint.resolve()
    assert training_model.train_calls[0]["resume"] is True
    assert Path(str(training_model.train_calls[0]["save_dir"])) == (
        Path(str(payload["run_dir"])) / "train"
    ).resolve()


def test_worker_uses_fit_epoch_callback_for_current_validation_metrics(tmp_path: Path):
    """Catches progress being written before the epoch's validation metrics exist."""
    manifest_path, payload = write_manifest(tmp_path)

    assert main(
        ["--manifest", str(manifest_path)],
        yolo_cls=FakeYOLO,
        cuda_available=lambda: False,
        project_root=tmp_path,
    ) == 0

    assert set(FakeYOLO.instances[0].callbacks) == {"on_fit_epoch_end"}
    progress = json.loads(
        (Path(str(payload["run_dir"])) / "progress.json").read_text(encoding="utf-8")
    )
    assert progress["current_epoch"] == 4
    assert progress["metrics"] == {"map50": 0.25}


def test_worker_uses_fixed_test_directory_when_metrics_has_no_save_dir(tmp_path: Path):
    """Catches reliance on a save_dir attribute absent from Ultralytics DetMetrics."""
    manifest_path, payload = write_manifest(tmp_path)
    FakeYOLO.fail_stage = "no_metrics_save_dir"

    assert main(
        ["--manifest", str(manifest_path)],
        yolo_cls=FakeYOLO,
        cuda_available=lambda: False,
        project_root=tmp_path,
    ) == 0
    assert (
        Path(str(payload["run_dir"])) / "confusion_matrix.png"
    ).read_bytes() == b"confusion matrix"


@pytest.mark.parametrize("stage", ["callback", "runtime", "device"])
def test_worker_converts_setup_failures_to_atomic_safe_results(
    tmp_path: Path, monkeypatch, stage: str
):
    """Catches setup/import/runtime failures escaping without terminal result.json."""
    manifest_path, payload = write_manifest(tmp_path)
    if stage == "callback":
        FakeYOLO.fail_stage = "callback"
        cuda_available = lambda: False
    elif stage == "runtime":
        monkeypatch.setattr(
            "training.worker.configure_runtime",
            lambda _root: (_ for _ in ()).throw(OSError(1455, "D:\\private\\dll")),
        )
        cuda_available = lambda: False
    else:
        cuda_available = lambda: (_ for _ in ()).throw(
            OSError(1455, "D:\\private\\cuda.dll")
        )

    assert main(
        ["--manifest", str(manifest_path)],
        yolo_cls=FakeYOLO,
        cuda_available=cuda_available,
        project_root=tmp_path,
    ) == 1
    text = (Path(str(payload["run_dir"])) / "result.json").read_text(encoding="utf-8")
    result = json.loads(text)
    assert result["error_code"] == "training_failed"
    assert str(tmp_path) not in text
    assert "private" not in text.casefold()


@pytest.mark.parametrize("stage", ["confusion_missing", "confusion_outside"])
def test_worker_fails_closed_for_missing_or_escaped_confusion_matrix(
    tmp_path: Path, stage: str
):
    """Catches completed jobs without their fixed held-out evaluation artifact."""
    manifest_path, payload = write_manifest(tmp_path)
    FakeYOLO.fail_stage = stage

    assert main(
        ["--manifest", str(manifest_path)],
        yolo_cls=FakeYOLO,
        cuda_available=lambda: False,
        project_root=tmp_path,
    ) == 1
    run_dir = Path(str(payload["run_dir"]))
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["error_code"] == "evaluation_failed"
    assert not (run_dir / "confusion_matrix.png").exists()


def test_worker_classifies_missing_best_as_training_artifact_failure(tmp_path: Path):
    """Catches absent training output being mislabeled as an invalid dataset."""
    manifest_path, payload = write_manifest(tmp_path)
    FakeYOLO.fail_stage = "missing_best"

    assert main(
        ["--manifest", str(manifest_path)],
        yolo_cls=FakeYOLO,
        cuda_available=lambda: False,
        project_root=tmp_path,
    ) == 1
    result = json.loads(
        (Path(str(payload["run_dir"])) / "result.json").read_text(encoding="utf-8")
    )
    assert result["error_code"] == "training_failed"


@pytest.mark.parametrize(
    ("stage", "expected_code"),
    [
        ("load", "weights_unavailable"),
        ("cuda", "cuda_oom"),
        ("dataset", "dataset_invalid"),
        ("train", "training_failed"),
        ("evaluation", "evaluation_failed"),
    ],
)
def test_worker_writes_only_stable_safe_failures(
    tmp_path: Path, stage: str, expected_code: str
):
    """Catches exception messages and server paths leaking into result artifacts."""
    manifest_path, payload = write_manifest(tmp_path)
    FakeYOLO.fail_stage = stage

    code = main(
        ["--manifest", str(manifest_path)],
        yolo_cls=FakeYOLO,
        cuda_available=lambda: True,
        project_root=tmp_path,
    )

    assert code == 1
    result_text = (Path(str(payload["run_dir"])) / "result.json").read_text(
        encoding="utf-8"
    )
    result = json.loads(result_text)
    assert result["status"] == "failed"
    assert result["error_code"] == expected_code
    assert result["error_message"]
    assert str(tmp_path) not in result_text
    assert "private" not in result_text.casefold()
    assert "\\" not in result_text


def test_worker_appends_safe_structured_failure_diagnostic(tmp_path: Path):
    manifest_path, payload = write_manifest(tmp_path)
    FakeYOLO.fail_stage = "train"

    assert main(
        ["--manifest", str(manifest_path)],
        yolo_cls=FakeYOLO,
        cuda_available=lambda: False,
        project_root=tmp_path,
    ) == 1

    log = (Path(str(payload["run_dir"])) / "training.log").read_text("utf-8")
    assert "stage=train code=training_failed exception=RuntimeError" in log
    assert "private" not in log.casefold()
    assert str(tmp_path) not in log


def test_worker_rejects_best_checkpoint_resolving_outside_run(tmp_path: Path):
    """Catches a symlinked checkpoint escaping the manager-owned run directory."""
    manifest_path, payload = write_manifest(tmp_path)
    outside = tmp_path / "outside.pt"
    outside.write_bytes(b"outside")
    weights = Path(str(payload["run_dir"])) / "train" / "weights"
    weights.mkdir(parents=True)
    link = weights / "best.pt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    class EscapingYOLO(FakeYOLO):
        def train(self, **kwargs):
            self.train_calls.append(kwargs)
            return SimpleNamespace(save_dir=weights.parent)

    assert main(
        ["--manifest", str(manifest_path)],
        yolo_cls=EscapingYOLO,
        cuda_available=lambda: False,
        project_root=tmp_path,
    ) == 1
    result = json.loads(
        (Path(str(payload["run_dir"])) / "result.json").read_text(encoding="utf-8")
    )
    assert result["error_code"] == "training_failed"
    assert not (Path(str(payload["run_dir"])) / "weights" / "best.pt").exists()
