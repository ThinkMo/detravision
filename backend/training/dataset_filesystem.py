from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
from typing import Mapping

from PIL import Image, UnidentifiedImageError


IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})


class DatasetFilesystemError(Exception):
    pass


@dataclass(frozen=True)
class ScannedImage:
    split: str
    image_key: str
    display_name: str
    width: int
    height: int
    content_digest: str
    has_label: bool


class DatasetFilesystem:
    def __init__(self, roots: Mapping[str, Path]) -> None:
        self._roots = {alias: path.resolve() for alias, path in roots.items()}

    def root(self, alias: str) -> Path:
        try:
            return self._roots[alias]
        except KeyError as exc:
            raise DatasetFilesystemError("unknown dataset root") from exc

    def dataset_path(self, dataset: Mapping[str, object]) -> Path:
        root = self.root(str(dataset["root_alias"]))
        relative = self._relative(str(dataset["directory"]), allow_dot=True)
        return self._contained(root, root / relative)

    def clone(self, source: Path, destination: Path) -> None:
        source = source.resolve()
        destination_parent = destination.parent.resolve()
        if not source.is_dir() or destination.exists():
            raise DatasetFilesystemError("dataset copy boundary is invalid")
        try:
            source.relative_to(destination_parent)
            destination.resolve().relative_to(destination_parent)
            destination.mkdir()
            for item in source.rglob("*"):
                if item.is_symlink():
                    raise DatasetFilesystemError("dataset symlink is not allowed")
                relative = item.relative_to(source)
                target = destination / relative
                if item.is_dir():
                    target.mkdir(exist_ok=True)
                elif item.suffix.casefold() != ".cache":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def scan(
        self, dataset: Mapping[str, object], split: str | None = None
    ) -> list[ScannedImage]:
        splits = (split,) if split is not None else ("train", "val", "test")
        if any(item not in {"train", "val", "test"} for item in splits):
            raise DatasetFilesystemError("invalid dataset split")
        root = self.root(str(dataset["root_alias"]))
        dataset_path = self.dataset_path(dataset)
        rows: list[ScannedImage] = []
        for current_split in splits:
            image_relative = self._relative(str(dataset[f"{current_split}_split"]))
            image_root = self._contained(root, dataset_path / image_relative)
            label_parts = list(PurePosixPath(image_relative).parts)
            try:
                label_parts[label_parts.index("images")] = "labels"
            except ValueError as exc:
                raise DatasetFilesystemError("invalid image split") from exc
            label_root = self._contained(root, dataset_path.joinpath(*label_parts))
            for path in sorted(image_root.rglob("*"), key=lambda value: value.as_posix().casefold()):
                if path.is_symlink():
                    raise DatasetFilesystemError("dataset symlink is not allowed")
                if not path.is_file() or path.suffix.casefold() not in IMAGE_EXTENSIONS:
                    continue
                safe_path = self._contained(root, path)
                key = safe_path.relative_to(image_root).as_posix()
                try:
                    with Image.open(safe_path) as image:
                        image.verify()
                    with Image.open(safe_path) as image:
                        width, height = image.size
                except (OSError, UnidentifiedImageError) as exc:
                    raise DatasetFilesystemError("dataset image is invalid") from exc
                digest = self._sha256(safe_path)
                label = self._contained(root, label_root / Path(key).with_suffix(".txt"))
                rows.append(ScannedImage(
                    split=current_split,
                    image_key=key,
                    display_name=Path(key).name,
                    width=width,
                    height=height,
                    content_digest=digest,
                    has_label=label.is_file(),
                ))
        return rows

    def resolve_image(
        self, dataset: Mapping[str, object], split: str, image_key: str
    ) -> Path:
        return self._resolve_item(dataset, split, image_key, labels=False)

    def resolve_label(
        self, dataset: Mapping[str, object], split: str, image_key: str
    ) -> Path:
        return self._resolve_item(dataset, split, image_key, labels=True)

    def _resolve_item(
        self, dataset: Mapping[str, object], split: str, image_key: str, *, labels: bool
    ) -> Path:
        if split not in {"train", "val", "test"}:
            raise DatasetFilesystemError("invalid dataset split")
        key = self._relative(image_key)
        relative = self._relative(str(dataset[f"{split}_split"]))
        parts = list(PurePosixPath(relative).parts)
        if labels:
            try:
                parts[parts.index("images")] = "labels"
            except ValueError as exc:
                raise DatasetFilesystemError("invalid image split") from exc
            key = str(PurePosixPath(key).with_suffix(".txt"))
        root = self.root(str(dataset["root_alias"]))
        return self._contained(root, self.dataset_path(dataset).joinpath(*parts, *PurePosixPath(key).parts))

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _relative(value: str, *, allow_dot: bool = False) -> str:
        if "\\" in value:
            raise DatasetFilesystemError("invalid relative dataset path")
        posix = PurePosixPath(value)
        windows = PureWindowsPath(value)
        if (
            posix.is_absolute() or windows.is_absolute() or bool(windows.drive)
            or any(part in {"", ".."} for part in posix.parts)
            or (not allow_dot and any(part == "." for part in posix.parts))
        ):
            raise DatasetFilesystemError("invalid relative dataset path")
        return posix.as_posix()

    @staticmethod
    def _contained(root: Path, path: Path) -> Path:
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
            return resolved
        except (OSError, ValueError) as exc:
            raise DatasetFilesystemError("dataset path escapes root") from exc
