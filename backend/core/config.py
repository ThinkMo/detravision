from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from numbers import Real
from pathlib import Path


_DATASET_ROOT_ALIAS = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _default_training_dataset_roots() -> str:
    return f"default={_PROJECT_ROOT / 'datasets'}"


def _parse_training_dataset_roots(value: str) -> tuple[tuple[str, Path], ...]:
    roots: list[tuple[str, Path]] = []
    aliases: set[str] = set()
    for raw_entry in value.split(";"):
        alias, separator, raw_path = raw_entry.partition("=")
        if (
            not separator
            or not _DATASET_ROOT_ALIAS.fullmatch(alias)
            or alias in aliases
            or not raw_path
        ):
            raise ValueError("TRAINING_DATASET_ROOTS has an invalid entry")
        path = Path(raw_path).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("TRAINING_DATASET_ROOTS paths must be existing directories")
        resolved = path.resolve()
        for _, existing in roots:
            if resolved == existing:
                raise ValueError("TRAINING_DATASET_ROOTS contains duplicate roots")
            if resolved in existing.parents or existing in resolved.parents:
                raise ValueError("TRAINING_DATASET_ROOTS contains nested roots")
        aliases.add(alias)
        roots.append((alias, resolved))
    if not roots:
        raise ValueError("TRAINING_DATASET_ROOTS must not be empty")
    return tuple(roots)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Safety Detect")
    environment: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data/safety_detect.db")
    session_ttl_hours: int = int(os.getenv("SESSION_TTL_HOURS", "12"))
    admin_initial_password: str | None = os.getenv("ADMIN_INITIAL_PASSWORD")
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
    cookie_secure: bool = _bool_env("COOKIE_SECURE", False)
    snapshot_dir: Path = Path(os.getenv("SNAPSHOT_DIR", "data/snapshots"))
    model_path: str | None = os.getenv("MODEL_PATH")
    annotation_font_path: Path | None = (
        Path(value) if (value := os.getenv("ANNOTATION_FONT_PATH")) else None
    )
    batch_retention_days: int = int(os.getenv("BATCH_RETENTION_DAYS", "30"))
    batch_worker_count: int = int(os.getenv("BATCH_WORKER_COUNT", "1"))
    worker_count: int = int(os.getenv("WORKER_COUNT", "1"))
    queue_size: int = int(os.getenv("FRAME_QUEUE_SIZE", "8"))
    reconcile_interval: float = float(os.getenv("RECONCILE_INTERVAL", "3"))
    enable_runtime: bool = _bool_env("ENABLE_RUNTIME", True)
    feishu_webhook: str | None = os.getenv("FEISHU_WEBHOOK")
    training_enabled: bool = _bool_env("TRAINING_ENABLED", True)
    training_dataset_yaml: Path = Path(
        os.getenv(
            "TRAINING_DATASET_YAML",
            "training/configs/construction-ppe-helmet.yaml",
        )
    )
    training_dataset_roots_raw: str = os.getenv(
        "TRAINING_DATASET_ROOTS", _default_training_dataset_roots()
    )
    training_dataset_roots: tuple[tuple[str, Path], ...] = field(init=False)
    training_run_dir: Path = Path(
        os.getenv("TRAINING_RUN_DIR", "data/training-runs")
    )
    training_model_cache_dir: Path = Path(
        os.getenv("TRAINING_MODEL_CACHE_DIR", "data/training-models")
    )
    training_log_tail_bytes: int = int(
        os.getenv("TRAINING_LOG_TAIL_BYTES", "65536")
    )
    training_stop_timeout_seconds: float = float(
        os.getenv("TRAINING_STOP_TIMEOUT_SECONDS", "15")
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "training_dataset_roots",
            _parse_training_dataset_roots(self.training_dataset_roots_raw),
        )
        if self.batch_retention_days < 1:
            raise ValueError("BATCH_RETENTION_DAYS must be positive")
        if self.batch_worker_count != 1:
            raise ValueError("BATCH_WORKER_COUNT must be 1 for this release")
        if (
            type(self.training_log_tail_bytes) is not int
            or self.training_log_tail_bytes < 1
        ):
            raise ValueError("TRAINING_LOG_TAIL_BYTES must be positive")
        if (
            isinstance(self.training_stop_timeout_seconds, bool)
            or not isinstance(self.training_stop_timeout_seconds, Real)
            or not math.isfinite(self.training_stop_timeout_seconds)
            or self.training_stop_timeout_seconds <= 0
        ):
            raise ValueError("TRAINING_STOP_TIMEOUT_SECONDS must be positive")

    @property
    def database_path(self) -> Path:
        """Compatibility adapter for callers that still require a SQLite path."""
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.removeprefix("sqlite:///"))
        return Path(os.getenv("DATABASE_PATH", "data/safety_detect.db"))


settings = Settings()
