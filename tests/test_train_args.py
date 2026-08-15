import pytest

from training.train import (
    parse_args,
    resolve_device,
    resume_kwargs,
    train_kwargs,
    validate_kwargs,
)


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
