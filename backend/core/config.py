from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    batch_retention_days: int = int(os.getenv("BATCH_RETENTION_DAYS", "30"))
    batch_worker_count: int = int(os.getenv("BATCH_WORKER_COUNT", "1"))
    worker_count: int = int(os.getenv("WORKER_COUNT", "1"))
    queue_size: int = int(os.getenv("FRAME_QUEUE_SIZE", "8"))
    reconcile_interval: float = float(os.getenv("RECONCILE_INTERVAL", "3"))
    enable_runtime: bool = _bool_env("ENABLE_RUNTIME", True)
    feishu_webhook: str | None = os.getenv("FEISHU_WEBHOOK")

    def __post_init__(self) -> None:
        if self.batch_retention_days < 1:
            raise ValueError("BATCH_RETENTION_DAYS must be positive")
        if self.batch_worker_count != 1:
            raise ValueError("BATCH_WORKER_COUNT must be 1 for this release")

    @property
    def database_path(self) -> Path:
        """Compatibility adapter for callers that still require a SQLite path."""
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.removeprefix("sqlite:///"))
        return Path(os.getenv("DATABASE_PATH", "data/safety_detect.db"))


settings = Settings()
