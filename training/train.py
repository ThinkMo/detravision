from __future__ import annotations

import argparse


def cache_value(value: str) -> bool | str:
    normalized = value.lower()
    if normalized in {"false", "no", "0"}:
        return False
    if normalized in {"ram", "disk"}:
        return normalized
    raise argparse.ArgumentTypeError("cache must be false, ram, or disk")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a Safety Detect YOLO model")
    parser.add_argument("--data", default="training/configs/safety.yaml")
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default=None, help="cpu, 0, 0,1; defaults to automatic selection")
    parser.add_argument("--workers", type=int, default=0, help="data loader workers; 0 is safest on Windows")
    parser.add_argument("--cache", type=cache_value, default=False, help="false, ram, or disk")
    return parser.parse_args()


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
