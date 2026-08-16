from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO
from uuid import uuid4

from .base import FileStorage


_SUFFIX = re.compile(r"[a-z0-9]{1,10}")


class LocalFileStorage(FileStorage):
    """Atomic local file storage confined to one configured directory."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    @staticmethod
    def _parts(value: str, *, label: str) -> tuple[str, ...]:
        if not value or "\x00" in value:
            raise ValueError(f"invalid {label}")
        normalized = value.replace("\\", "/")
        posix = PurePosixPath(normalized)
        windows = PureWindowsPath(value)
        if posix.is_absolute() or windows.is_absolute() or windows.drive:
            raise ValueError(f"absolute {label} is not allowed")
        if any(part in {"", ".", ".."} for part in posix.parts):
            raise ValueError(f"path traversal in {label}")
        return posix.parts

    def _path(self, key: str) -> Path:
        parts = self._parts(key, label="storage key")
        path = self.root.joinpath(*parts).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("storage key escapes configured root") from exc
        return path

    def save(self, stream: BinaryIO, namespace: str, suffix: str) -> str:
        namespace_parts = self._parts(namespace, label="namespace")
        normalized_suffix = suffix.removeprefix(".").casefold()
        if _SUFFIX.fullmatch(normalized_suffix) is None:
            raise ValueError("invalid file suffix")

        today = datetime.now(timezone.utc)
        key_parts = (
            *namespace_parts,
            f"{today:%Y}",
            f"{today:%m}",
            f"{today:%d}",
            f"{uuid4().hex}.{normalized_suffix}",
        )
        key = PurePosixPath(*key_parts).as_posix()
        final_path = self._path(key)
        final_path.parent.mkdir(parents=True, exist_ok=True)

        descriptor, temporary_name = tempfile.mkstemp(
            dir=final_path.parent,
            prefix=".upload-",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as destination:
                while chunk := stream.read(1024 * 1024):
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary_path, final_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return key

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
