from pathlib import Path

from training.prepare_construction_ppe import convert_dataset


def _write_sample(root: Path, split: str, stem: str, labels: list[str]) -> None:
    image_dir = root / "images" / split
    label_dir = root / "labels" / split
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)
    (image_dir / f"{stem}.jpg").write_bytes(b"image")
    (label_dir / f"{stem}.txt").write_text("\n".join(labels) + "\n", encoding="utf-8")


def test_convert_dataset_remaps_target_classes_and_drops_irrelevant_images(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    _write_sample(
        source,
        "train",
        "relevant",
        [
            "6 0.5 0.5 0.8 0.9",  # Person -> person
            "0 0.5 0.2 0.2 0.2",  # helmet -> helmet
            "7 0.2 0.2 0.1 0.1",  # no_helmet -> no_helmet
            "1 0.4 0.4 0.1 0.1",  # gloves -> discarded
        ],
    )
    _write_sample(source, "train", "irrelevant", ["1 0.5 0.5 0.2 0.2"])

    stats = convert_dataset(source, destination)

    assert (destination / "images" / "train" / "relevant.jpg").exists()
    assert not (destination / "images" / "train" / "irrelevant.jpg").exists()
    assert (destination / "labels" / "train" / "relevant.txt").read_text(encoding="utf-8").splitlines() == [
        "0 0.5 0.5 0.8 0.9",
        "1 0.5 0.2 0.2 0.2",
        "2 0.2 0.2 0.1 0.1",
    ]
    assert stats["train"] == {"images": 1, "person": 1, "helmet": 1, "no_helmet": 1}
