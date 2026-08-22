from __future__ import annotations

import json
import os
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
from training.worker import main


def metric_fixture(
    *,
    map50: float = 0.85,
    no_helmet_precision: float = 0.85,
    no_helmet_recall: float = 0.90,
):
    return SimpleNamespace(
        names={0: "person", 1: "helmet", 2: "no_helmet"},
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
        self.trainer = SimpleNamespace(epoch=0, metrics={"metrics/mAP50(B)": 0.25})
        self.__class__.instances.append(self)

    def add_callback(self, name: str, callback):
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
        self.callbacks["on_train_epoch_end"](self.trainer)
        weights = Path(str(kwargs["project"])) / str(kwargs["name"]) / "weights"
        weights.mkdir(parents=True)
        (weights / "best.pt").write_bytes(b"best checkpoint")
        (weights / "last.pt").write_bytes(b"last checkpoint")
        return SimpleNamespace(save_dir=weights.parent)

    def val(self, **kwargs):
        self.val_calls.append(kwargs)
        if self.fail_stage == "evaluation":
            raise RuntimeError("D:\\private\\test image failed")
        return metric_fixture()


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
            "batch": -1,
            "device": 0,
            "workers": 0,
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
    assert os.environ["YOLO_CONFIG_DIR"] == str(tmp_path / "data")
    assert os.environ["MPLCONFIGDIR"] == str(tmp_path / "data" / "matplotlib")


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
