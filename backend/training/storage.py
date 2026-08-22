from __future__ import annotations

import re
import secrets
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml

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
_TAIL_OVERLAP_BYTES = 8192
_TRACEBACK_FILE_LINE = re.compile(r"^\s*File\s+['\"]")
_WINDOWS_DRIVE_PATH = re.compile(
    r"(?i)(?<![a-z0-9_])[a-z]:[\\/][^\r\n\t\s'\"<>|]*"
)
_UNC_PATH = re.compile(r"\\\\[^\r\n\t\s\\/'\"<>|]+\\[^\r\n\t\s'\"<>|]+")
_POSIX_PATH = re.compile(
    r"(?<![a-zA-Z0-9_:/.])/(?:[^\r\n\t\s/'\"<>|]+/)*[^\r\n\t\s/'\"<>|]+"
)
_FILE_URI = re.compile(r"(?i)\bfile://[^\r\n\t\s]+")
_SLASH_UNC_PATH = re.compile(
    r"(?<![\w:/])//[^\r\n\t\s/]+/[^\r\n\t\s]+"
)
_DIAGNOSTIC_STAGE = frozenset({"launch", "monitor", "result", "shutdown"})
_DIAGNOSTIC_CODE = frozenset({"training_failed", "worker_exit", "interrupted"})
_DIAGNOSTIC_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")


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

    def append_safe_diagnostic(
        self,
        run_key: str,
        stage: str,
        code: str,
        *,
        exception_type: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        if stage not in _DIAGNOSTIC_STAGE or code not in _DIAGNOSTIC_CODE:
            return
        fields = [f"stage={stage}", f"code={code}"]
        if exception_type and _DIAGNOSTIC_NAME.fullmatch(exception_type):
            fields.append(f"exception={exception_type}")
        if type(exit_code) is int:
            fields.append(f"exit_code={max(-999999, min(999999, exit_code))}")
        line = ("training_diagnostic " + " ".join(fields) + "\n").encode("utf-8")[:256]
        try:
            path = self.resolve_artifact(run_key, "log")
            with path.open("ab") as stream:
                stream.write(line)
        except (OSError, ValueError):
            return

    def write_dataset_snapshot(self, run_key: str, dataset: dict[str, object]) -> Path:
        run_dir = self.run_path(run_key)
        destination = self._contained(run_dir / "dataset.yaml")
        temporary = self._contained(
            run_dir / f".dataset-{secrets.token_hex(8)}.tmp"
        )
        classes = dataset.get("classes")
        if not isinstance(classes, list) or not all(
            isinstance(item, str) for item in classes
        ):
            raise ValueError("invalid dataset classes")
        dataset_path = Path(dataset["path"]).resolve()
        if not dataset_path.is_absolute() or not dataset_path.is_dir():
            raise ValueError("invalid dataset path")
        splits: dict[str, str] = {}
        for key in ("train", "val", "test"):
            value = dataset.get(key)
            if not isinstance(value, str) or "\\" in value:
                raise ValueError("invalid dataset split")
            posix = PurePosixPath(value)
            windows = PureWindowsPath(value)
            if (
                posix.is_absolute()
                or windows.is_absolute()
                or bool(windows.drive)
                or any(part in {"", ".", ".."} for part in posix.parts)
            ):
                raise ValueError("invalid dataset split")
            splits[key] = posix.as_posix()
        payload = {
            "path": str(dataset_path),
            **splits,
            "names": dict(enumerate(classes)),
        }
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                yaml.safe_dump(payload, stream, allow_unicode=True, sort_keys=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return destination

    def tail_log(self, run_key: str, limit: int) -> str:
        if type(limit) is not int or limit < 1:
            raise ValueError("log tail limit must be positive")
        byte_limit = min(limit, self.log_tail_bytes)
        path = self.resolve_artifact(run_key, "log")
        try:
            with path.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                start = max(0, size - byte_limit - _TAIL_OVERLAP_BYTES)
                stream.seek(start)
                payload = stream.read(byte_limit + _TAIL_OVERLAP_BYTES)
        except FileNotFoundError:
            return ""
        if start:
            _, separator, payload = payload.partition(b"\n")
            if not separator:
                return ""
        scrubbed = self.scrub_log(payload.decode("utf-8", errors="replace"))
        encoded = scrubbed.encode("utf-8")[-byte_limit:]
        return encoded.decode("utf-8", errors="ignore")

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
                    re.escape(configured_path)
                    + r"(?:[\\/][^\r\n\t\s'\"<>|]*)*",
                    "[redacted]",
                    scrubbed,
                    flags=re.IGNORECASE,
                )
        safe_lines: list[str] = []
        redact_source_line = False
        for line in scrubbed.splitlines(keepends=True):
            if _TRACEBACK_FILE_LINE.match(line):
                ending = "\n" if line.endswith(("\n", "\r")) else ""
                safe_lines.append(f"[traceback location redacted]{ending}")
                redact_source_line = True
                continue
            if redact_source_line and line[:1].isspace():
                ending = "\n" if line.endswith(("\n", "\r")) else ""
                safe_lines.append(f"[traceback source redacted]{ending}")
                continue
            redact_source_line = False
            if (
                _FILE_URI.search(line)
                or _SLASH_UNC_PATH.search(line)
                or _WINDOWS_DRIVE_PATH.search(line)
                or _UNC_PATH.search(line)
                or _POSIX_PATH.search(line)
            ):
                ending = "\n" if line.endswith(("\n", "\r")) else ""
                safe_lines.append(f"[path redacted]{ending}")
                continue
            line = _UNC_PATH.sub("[redacted]", line)
            line = _WINDOWS_DRIVE_PATH.sub("[redacted]", line)
            line = _POSIX_PATH.sub("[redacted]", line)
            safe_lines.append(line)
        return "".join(safe_lines)
