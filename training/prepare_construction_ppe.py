from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


CLASS_MAP = {6: 0, 0: 1, 7: 2}
CLASS_NAMES = {0: "person", 1: "helmet", 2: "no_helmet"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def convert_dataset(source: Path, destination: Path) -> dict[str, dict[str, int]]:
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")

    stats: dict[str, dict[str, int]] = {}
    for split in ("train", "val", "test"):
        source_images = source / "images" / split
        source_labels = source / "labels" / split
        if not source_images.is_dir() or not source_labels.is_dir():
            continue

        target_images = destination / "images" / split
        target_labels = destination / "labels" / split
        target_images.mkdir(parents=True, exist_ok=True)
        target_labels.mkdir(parents=True, exist_ok=True)
        split_stats = {"images": 0, "person": 0, "helmet": 0, "no_helmet": 0}

        for image_path in sorted(source_images.iterdir()):
            if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            label_path = source_labels / f"{image_path.stem}.txt"
            if not label_path.exists():
                continue

            mapped_lines = []
            mapped_counts = {"person": 0, "helmet": 0, "no_helmet": 0}
            for line_number, line in enumerate(label_path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) != 5:
                    raise ValueError(f"invalid YOLO label at {label_path}:{line_number}")
                source_class = int(parts[0])
                if source_class not in CLASS_MAP:
                    continue
                target_class = CLASS_MAP[source_class]
                mapped_lines.append(" ".join((str(target_class), *parts[1:])))
                mapped_counts[CLASS_NAMES[target_class]] += 1

            if not mapped_lines:
                continue
            shutil.copy2(image_path, target_images / image_path.name)
            (target_labels / f"{image_path.stem}.txt").write_text(
                "\n".join(mapped_lines) + "\n", encoding="utf-8"
            )
            split_stats["images"] += 1
            for name, count in mapped_counts.items():
                split_stats[name] += count

        stats[split] = split_stats
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare helmet-only classes from Construction-PPE")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(convert_dataset(args.source, args.destination), indent=2))


if __name__ == "__main__":
    main()
