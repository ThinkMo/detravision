from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence


DEFAULT_DATA = "training/configs/construction-ppe-helmet.yaml"


def cache_value(value: str) -> bool | str:
    normalized = value.lower()
    if normalized in {"false", "no", "0"}:
        return False
    if normalized in {"ram", "disk"}:
        return normalized
    raise argparse.ArgumentTypeError("cache must be false, ram, or disk")


def add_device_options(parser: argparse.ArgumentParser, *, include_cache: bool) -> None:
    parser.add_argument("--device", default="auto", help="auto, cpu, 0, 0,1, etc.")
    parser.add_argument("--workers", type=int, default=0)
    if include_cache:
        parser.add_argument("--cache", type=cache_value, default=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train, resume, or validate a Safety Detect YOLO model"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    train = subparsers.add_parser("train", help="start a new training run")
    train.add_argument("--data", default=DEFAULT_DATA)
    train.add_argument("--model", default="yolo11n.pt")
    train.add_argument("--epochs", type=int, default=50)
    train.add_argument("--imgsz", type=int, default=512)
    train.add_argument("--batch", type=int, default=4)
    train.add_argument("--project", default="runs/detect")
    train.add_argument("--name")
    train.add_argument("--patience", type=int, default=100)
    train.add_argument("--seed", type=int, default=0)
    add_device_options(train, include_cache=True)

    resume = subparsers.add_parser("resume", help="resume from a last.pt checkpoint")
    resume.add_argument("checkpoint")
    resume.add_argument("--epochs", type=int)
    add_device_options(resume, include_cache=True)

    validate = subparsers.add_parser("validate", help="evaluate a checkpoint")
    validate.add_argument("checkpoint")
    validate.add_argument("--data", default=DEFAULT_DATA)
    validate.add_argument("--split", choices=("val", "test"), default="val")
    validate.add_argument("--imgsz", type=int, default=512)
    validate.add_argument("--batch", type=int, default=4)
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


def main() -> None:
    args = parse_args()
    import torch
    from ultralytics import YOLO

    device = args.device if args.device is not None else (0 if torch.cuda.is_available() else "cpu")
    YOLO(args.model).train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        workers=args.workers,
        cache=args.cache,
    )


if __name__ == "__main__":
    main()
