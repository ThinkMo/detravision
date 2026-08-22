from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from training.contracts import extract_metrics


DEFAULT_DATA = "training/configs/construction-ppe-helmet.yaml"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def cache_value(value: str) -> bool | str:
    normalized = value.lower()
    if normalized in {"false", "no", "0"}:
        return False
    if normalized in {"ram", "disk"}:
        return normalized
    raise argparse.ArgumentTypeError("cache must be false, ram, or disk")


def add_device_options(parser: argparse.ArgumentParser, *, include_cache: bool) -> None:
    parser.add_argument("--device", default="auto", help="auto, cpu, 0, 0,1, etc.")
    parser.add_argument("--workers", type=int, default=0, help="data-loading worker count")
    if include_cache:
        parser.add_argument("--cache", type=cache_value, default=False, help="false, ram, or disk")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train, resume, or validate a Safety Detect YOLO model"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    train = subparsers.add_parser("train", help="start a new training run")
    train.add_argument("--data", default=DEFAULT_DATA, help="dataset YAML path")
    train.add_argument("--model", default="yolo11n.pt", help="model checkpoint or YAML")
    train.add_argument("--epochs", type=int, default=50, help="training epoch count")
    train.add_argument("--imgsz", type=int, default=512, help="square input image size")
    train.add_argument("--batch", type=int, default=4, help="images per batch")
    train.add_argument("--project", default="runs/detect", help="base output directory")
    train.add_argument("--name", help="optional run directory name")
    train.add_argument("--patience", type=int, default=100, help="early-stopping patience")
    train.add_argument("--seed", type=int, default=0, help="random seed")
    add_device_options(train, include_cache=True)

    resume = subparsers.add_parser("resume", help="resume from a last.pt checkpoint")
    resume.add_argument("checkpoint", help="last.pt checkpoint to resume")
    resume.add_argument("--epochs", type=int, help="new total epoch target")
    add_device_options(resume, include_cache=True)

    validate = subparsers.add_parser("validate", help="evaluate a checkpoint")
    validate.add_argument("checkpoint", help="checkpoint to evaluate")
    validate.add_argument("--data", default=DEFAULT_DATA, help="dataset YAML path")
    validate.add_argument("--split", choices=("val", "test"), default="val", help="dataset split to evaluate")
    validate.add_argument("--imgsz", type=int, default=512, help="square input image size")
    validate.add_argument("--batch", type=int, default=4, help="images per batch")
    add_device_options(validate, include_cache=False)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def resolve_device(value: str, cuda_available: Callable[[], bool]) -> int | str:
    if value != "auto":
        return value
    return 0 if cuda_available() else "cpu"


def train_kwargs(args: argparse.Namespace, device: int | str) -> dict[str, object]:
    values = {
        "data": args.data, "epochs": args.epochs, "imgsz": args.imgsz,
        "batch": args.batch, "device": device, "workers": args.workers,
        "cache": args.cache, "project": args.project,
        "patience": args.patience, "seed": args.seed,
    }
    if args.name is not None:
        values["name"] = args.name
    return values


def resume_kwargs(args: argparse.Namespace, device: int | str) -> dict[str, object]:
    values = {
        "resume": True, "device": device, "workers": args.workers,
        "cache": args.cache,
    }
    if args.epochs is not None:
        values["epochs"] = args.epochs
    return values


def validate_kwargs(args: argparse.Namespace, device: int | str) -> dict[str, object]:
    return {
        "data": args.data, "split": args.split, "imgsz": args.imgsz,
        "batch": args.batch, "device": device, "workers": args.workers,
    }


def configure_runtime(project_root: Path = PROJECT_ROOT) -> None:
    data_dir = project_root / "data"
    matplotlib_dir = data_dir / "matplotlib"
    matplotlib_dir.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(data_dir)
    os.environ["MPLCONFIGDIR"] = str(matplotlib_dir)


def required_file(value: str, label: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return str(path)


def validate_local_paths(args: argparse.Namespace) -> None:
    if args.command in {"train", "validate"}:
        args.data = required_file(args.data, "dataset YAML")
    if args.command in {"resume", "validate"}:
        args.checkpoint = required_file(args.checkpoint, "checkpoint")


def format_validation_metrics(metrics: object) -> str:
    extracted = extract_metrics(metrics)
    lines = [
        "overall: "
        f"precision={extracted['precision']:.4f}, recall={extracted['recall']:.4f}, "
        f"mAP50={extracted['map50']:.4f}, mAP50-95={extracted['map50_95']:.4f}"
    ]
    for name, class_metrics in extracted["classes"].items():
        lines.append(
            f"{name}: mAP50={class_metrics['map50']:.4f}, "
            f"mAP50-95={class_metrics['map50_95']:.4f}"
        )
    return "\n".join(lines)


def execute(
    args: argparse.Namespace,
    *,
    yolo_cls=None,
    cuda_available=None,
    project_root: Path = PROJECT_ROOT,
):
    validate_local_paths(args)
    configure_runtime(project_root)
    if cuda_available is None:
        import torch
        cuda_available = torch.cuda.is_available
    if yolo_cls is None:
        from ultralytics import YOLO
        yolo_cls = YOLO

    device = resolve_device(args.device, cuda_available)
    if args.command == "train":
        return yolo_cls(args.model).train(**train_kwargs(args, device))
    if args.command == "resume":
        return yolo_cls(args.checkpoint).train(**resume_kwargs(args, device))
    metrics = yolo_cls(args.checkpoint).val(**validate_kwargs(args, device))
    print(format_validation_metrics(metrics))
    return metrics


def main(
    argv: Sequence[str] | None = None,
    *,
    yolo_cls=None,
    cuda_available=None,
    project_root: Path = PROJECT_ROOT,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        execute(
            args,
            yolo_cls=yolo_cls,
            cuda_available=cuda_available,
            project_root=project_root,
        )
    except FileNotFoundError as exc:
        parser.error(str(exc))
    except Exception as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
