from __future__ import annotations

import re
import secrets
from pathlib import Path, PurePosixPath, PureWindowsPath

from backend.core.config import Settings


ARTIFACTS = {
    "log": "training.log",
    "progress": "progress.json",
    "result": "result.json",
    "best": "weights/best.pt",
    "last": "weights/last.pt",
    "confusion_matrix": "confusion_matrix.png",
}

_RUN_KEY = re.compile(r"run-[0-9a-f]{32}\Z")


class TrainingStorage:
    """Local training artifacts confined to an opaque per-run directory."""

    def __init__(self, settings: Settings) -> None:
        self.root = settings.training_run_dir.resolve()
        self.model_cache_root = settings.training_model_cache_dir.resolve()
        self.dataset_yaml = settings.training_dataset_yaml.resolve()
        self.log_tail_bytes = settings.training_log_tail_bytes

    @staticmethod
    def _validate_run_key(run_key: str) -> None:
        posix = PurePosixPath(run_key.replace("\\", "/"))
        windows = PureWindowsPath(run_key)
        if (
            _RUN_KEY.fullmatch(run_key) is None
            or posix.is_absolute()
            or windows.is_absolute()
            or bool(windows.drive)
        ):
            raise ValueError("invalid training run key")

    def _contained(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("training path escapes configured root") from exc
        return resolved

    def run_path(self, run_key: str) -> Path:
        self._validate_run_key(run_key)
        return self._contained(self.root / run_key)

    def create_run(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        while True:
            run_key = f"run-{secrets.token_hex(16)}"
            run_dir = self.root / run_key
            try:
                run_dir.mkdir()
            except FileExistsError:
                continue
            self._contained(run_dir)
            return run_key

    def resolve_artifact(self, run_key: str, kind: str) -> Path:
        try:
            relative_name = ARTIFACTS[kind]
        except KeyError as exc:
            raise ValueError("unsupported training artifact") from exc
        return self._contained(self.run_path(run_key) / relative_name)

    def tail_log(self, run_key: str, limit: int) -> str:
        if isinstance(limit, bool) or limit < 1:
            raise ValueError("log tail limit must be positive")
        byte_limit = min(limit, self.log_tail_bytes)
        path = self.resolve_artifact(run_key, "log")
        try:
            with path.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - byte_limit))
                payload = stream.read(byte_limit)
        except FileNotFoundError:
            return ""
        return self.scrub_log(payload.decode("utf-8", errors="replace"))

    def scrub_log(self, text: str) -> str:
        configured_paths = {
            str(self.dataset_yaml),
            self.dataset_yaml.as_posix(),
            str(self.dataset_yaml.parent),
            self.dataset_yaml.parent.as_posix(),
            str(self.model_cache_root),
            self.model_cache_root.as_posix(),
            str(self.root),
            self.root.as_posix(),
        }
        scrubbed = text
        for configured_path in sorted(configured_paths, key=len, reverse=True):
            if configured_path:
                scrubbed = re.sub(
                    re.escape(configured_path),
                    "[redacted]",
                    scrubbed,
                    flags=re.IGNORECASE,
                )
        return scrubbed
