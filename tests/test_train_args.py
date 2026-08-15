import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from training.train import (
    parse_args,
    resolve_device,
    resume_kwargs,
    train_kwargs,
    validate_kwargs,
)
from training.train import configure_runtime, execute, format_validation_metrics, main


class FakeYOLO:
    instances = []

    def __init__(self, model):
        self.model = model
        self.train_calls = []
        self.val_calls = []
        self.__class__.instances.append(self)

    def train(self, **kwargs):
        self.train_calls.append(kwargs)
        return "trained"

    def val(self, **kwargs):
        self.val_calls.append(kwargs)
        return SimpleNamespace(
            names={0: "person", 1: "helmet", 2: "no_helmet"},
            box=SimpleNamespace(
                mp=0.65, mr=0.60, map50=0.70, map=0.36,
                ap50=[0.81, 0.92, 0.24], maps=[0.49, 0.50, 0.09],
            ),
        )


@pytest.fixture(autouse=True)
def clear_fake_yolo_instances():
    FakeYOLO.instances.clear()


def test_subcommand_is_required():
    with pytest.raises(SystemExit) as exc_info:
        parse_args([])
    assert exc_info.value.code == 2


def test_train_defaults_are_current_and_windows_safe():
    args = parse_args(["train"])
    assert args.command == "train"
    assert args.data == "training/configs/construction-ppe-helmet.yaml"
    assert args.model == "yolo11n.pt"
    assert args.epochs == 50
    assert args.imgsz == 512
    assert args.batch == 4
    assert args.device == "auto"
    assert args.workers == 0
    assert args.cache is False
    assert args.project == "runs/detect"
    assert args.name is None
    assert args.patience == 100
    assert args.seed == 0


def test_train_overrides_are_preserved():
    args = parse_args([
        "train", "--data", "custom.yaml", "--model", "custom.pt",
        "--epochs", "10", "--imgsz", "640", "--batch", "2",
        "--device", "cpu", "--workers", "2", "--cache", "ram",
        "--project", "output", "--name", "experiment",
        "--patience", "20", "--seed", "7",
    ])
    assert train_kwargs(args, "cpu") == {
        "data": "custom.yaml", "epochs": 10, "imgsz": 640, "batch": 2,
        "device": "cpu", "workers": 2, "cache": "ram",
        "project": "output", "name": "experiment", "patience": 20,
        "seed": 7,
    }


def test_resume_accepts_checkpoint_and_total_epoch_override():
    args = parse_args([
        "resume", "runs/job/weights/last.pt", "--epochs", "80",
        "--device", "0", "--workers", "1", "--cache", "disk",
    ])
    assert args.checkpoint == "runs/job/weights/last.pt"
    assert resume_kwargs(args, "0") == {
        "resume": True, "device": "0", "workers": 1,
        "cache": "disk", "epochs": 80,
    }


def test_resume_omits_epoch_override_by_default():
    args = parse_args(["resume", "last.pt"])
    assert "epochs" not in resume_kwargs(args, "cpu")


def test_validate_accepts_only_validation_or_test_split():
    args = parse_args(["validate", "best.pt", "--split", "test"])
    assert args.split == "test"
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["validate", "best.pt", "--split", "train"])
    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("requested", "available", "expected"),
    [("auto", True, 0), ("auto", False, "cpu"), ("cpu", True, "cpu"), ("0,1", True, "0,1")],
)
def test_device_resolution(requested, available, expected):
    assert resolve_device(requested, lambda: available) == expected


def test_runtime_configuration_uses_project_local_directories(tmp_path, monkeypatch):
    monkeypatch.delenv("YOLO_CONFIG_DIR", raising=False)
    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    configure_runtime(tmp_path)
    assert os.environ["YOLO_CONFIG_DIR"] == str(tmp_path / "data")
    assert os.environ["MPLCONFIGDIR"] == str(tmp_path / "data" / "matplotlib")
    assert (tmp_path / "data" / "matplotlib").is_dir()


def test_helmet_dataset_yaml_resolves_test_images_from_project_datasets_root(tmp_path, monkeypatch):
    expected_test_path = tmp_path / "datasets" / "construction-ppe-helmet" / "images" / "test"
    for split in ("train", "val", "test"):
        (tmp_path / "datasets" / "construction-ppe-helmet" / "images" / split).mkdir(parents=True)

    monkeypatch.setenv("YOLO_CONFIG_DIR", str(tmp_path / "ultralytics"))
    monkeypatch.chdir(tmp_path)
    from ultralytics.data.utils import check_det_dataset

    yaml_path = Path(__file__).parents[1] / "training" / "configs" / "construction-ppe-helmet.yaml"
    dataset = check_det_dataset(str(yaml_path), autodownload=False, split="test")

    assert Path(dataset["test"]) == expected_test_path


def test_missing_training_data_fails_before_model_load(tmp_path):
    args = parse_args(["train", "--data", str(tmp_path / "missing.yaml")])
    with pytest.raises(FileNotFoundError, match="dataset YAML"):
        execute(args, yolo_cls=FakeYOLO, cuda_available=lambda: False, project_root=tmp_path)
    assert FakeYOLO.instances == []


def test_train_dispatches_expected_arguments(tmp_path):
    data = tmp_path / "data.yaml"
    data.write_text("names: {0: person}", encoding="utf-8")
    args = parse_args(["train", "--data", str(data), "--name", "formal"])
    result = execute(args, yolo_cls=FakeYOLO, cuda_available=lambda: True, project_root=tmp_path)
    assert result == "trained"
    model = FakeYOLO.instances[0]
    assert model.model == "yolo11n.pt"
    assert model.train_calls[0]["device"] == 0
    assert model.train_calls[0]["name"] == "formal"
    assert Path(model.train_calls[0]["data"]).is_absolute()


def test_resume_requires_checkpoint_and_dispatches_resume(tmp_path):
    checkpoint = tmp_path / "last.pt"
    checkpoint.touch()
    args = parse_args(["resume", str(checkpoint), "--epochs", "80"])
    execute(args, yolo_cls=FakeYOLO, cuda_available=lambda: False, project_root=tmp_path)
    model = FakeYOLO.instances[0]
    assert Path(model.model) == checkpoint.resolve()
    assert model.train_calls == [{
        "resume": True, "device": "cpu", "workers": 0,
        "cache": False, "epochs": 80,
    }]


def test_validate_dispatches_and_returns_metrics(tmp_path):
    checkpoint = tmp_path / "best.pt"
    checkpoint.touch()
    data = tmp_path / "data.yaml"
    data.write_text("names: {0: person}", encoding="utf-8")
    args = parse_args([
        "validate", str(checkpoint), "--data", str(data), "--split", "test",
    ])
    metrics = execute(args, yolo_cls=FakeYOLO, cuda_available=lambda: False, project_root=tmp_path)
    model = FakeYOLO.instances[0]
    assert model.val_calls[0]["split"] == "test"
    assert model.val_calls[0]["device"] == "cpu"
    assert metrics.box.map50 == 0.70


def test_validation_summary_contains_overall_and_per_class_metrics():
    metrics = FakeYOLO("unused").val()
    summary = format_validation_metrics(metrics)
    assert "precision=0.6500" in summary
    assert "mAP50=0.7000" in summary
    assert "person: mAP50=0.8100, mAP50-95=0.4900" in summary
    assert "no_helmet: mAP50=0.2400, mAP50-95=0.0900" in summary


def test_validation_summary_maps_sparse_class_metrics_to_configured_class_ids():
    metrics = SimpleNamespace(
        names={0: "person", 1: "helmet", 2: "no_helmet"},
        box=SimpleNamespace(
            mp=0.65,
            mr=0.60,
            map50=0.70,
            map=0.36,
            ap_class_index=[1, 2],
            ap50=[0.92, 0.24],
            maps=[0.36, 0.50, 0.09],
        ),
    )

    summary = format_validation_metrics(metrics)

    assert "helmet: mAP50=0.9200, mAP50-95=0.5000" in summary
    assert "no_helmet: mAP50=0.2400, mAP50-95=0.0900" in summary
    assert "person:" not in summary


def test_main_uses_argparse_exit_code_for_missing_checkpoint(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        main(
            ["resume", str(tmp_path / "missing.pt")],
            yolo_cls=FakeYOLO,
            cuda_available=lambda: False,
            project_root=tmp_path,
        )
    assert exc_info.value.code == 2


class FailingYOLO(FakeYOLO):
    def train(self, **kwargs):
        raise RuntimeError("CUDA failed")


def test_main_reports_runtime_failure(tmp_path, capsys):
    data = tmp_path / "data.yaml"
    data.write_text("names: {0: person}", encoding="utf-8")
    code = main(
        ["train", "--data", str(data)],
        yolo_cls=FailingYOLO,
        cuda_available=lambda: True,
        project_root=tmp_path,
    )
    assert code == 1
    assert "train failed: CUDA failed" in capsys.readouterr().err
