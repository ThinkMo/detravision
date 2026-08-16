from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.storage import FileStorage

from .repository import BatchRepository


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CleanupSummary:
    scanned_jobs: int = 0
    deleted_jobs: int = 0
    failed_jobs: int = 0


class RetentionCleaner:
    def __init__(
        self,
        repository: BatchRepository,
        storage: FileStorage,
        *,
        interval_seconds: float = 6 * 60 * 60,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._repository = repository
        self._storage = storage
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.run_once(now=datetime.now(timezone.utc))
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="batch-retention-cleaner",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def run_once(self, now: datetime) -> CleanupSummary:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        expired = self._repository.list_expired(now)
        deleted_jobs = 0
        failed_jobs = 0
        for job in expired:
            failed = False
            keys = [
                key
                for image in job.images
                for key in (image.source_key, image.annotated_key)
                if key is not None
            ]
            for key in dict.fromkeys(keys):
                try:
                    self._storage.delete(key)
                except FileNotFoundError:
                    pass
                except Exception:
                    failed = True
                    logger.warning(
                        "batch_retention_delete_failed job_id=%s key=%s error_code=storage_delete_failed",
                        job.id,
                        key,
                    )
            if failed:
                failed_jobs += 1
                continue
            try:
                if self._repository.delete_expired_job(job.id, now=now):
                    deleted_jobs += 1
            except Exception:
                failed_jobs += 1
                logger.warning(
                    "batch_retention_database_delete_failed job_id=%s error_code=database_delete_failed",
                    job.id,
                )
        return CleanupSummary(
            scanned_jobs=len(expired),
            deleted_jobs=deleted_jobs,
            failed_jobs=failed_jobs,
        )

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            try:
                self.run_once(now=datetime.now(timezone.utc))
            except Exception:
                logger.error("batch_retention_run_failed error_code=cleanup_failed")
