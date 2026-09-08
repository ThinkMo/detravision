from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export and version a DetraVision model")
    parser.add_argument("model")
    parser.add_argument("--target", choices=("cpu", "gpu"), default="cpu")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--map50", type=float)
    parser.add_argument("--data-version", default="unknown")
    parser.add_argument("--output", default="model_repo")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    from ultralytics import YOLO

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.target == "gpu":
        exported = Path(YOLO(args.model).export(format="engine", half=True, imgsz=args.imgsz))
        runtime = "tensorrt-fp16"
    else:
        exported = Path(YOLO(args.model).export(format="onnx", half=False, imgsz=args.imgsz))
        runtime = "onnx-fp32"
    destination = output / f"best_{stamp}{exported.suffix}"
    shutil.copy2(exported, destination)
    metadata = {
        "name": destination.stem,
        "file_path": str(destination),
        "runtime": runtime,
        "map50": args.map50,
        "data_version": args.data_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (output / f"{destination.stem}.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(destination)


if __name__ == "__main__":
    main()
