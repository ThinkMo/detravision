from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Protocol

from training.contracts import TrainingProgress, TrainingResult

from .repository import TrainingRepository
from .storage import TrainingStorage


class ProcessHandle(Protocol):
    pid: int
    returncode: int | None

    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


@dataclass(slots=True)
class _ActiveProcess:
    job_id: int
    run_key: str
    process: ProcessHandle
    log: BinaryIO
    progress_bytes: bytes | None = None


class LocalTrainingManager:
    """Own one local training child and serialize queued GPU work."""

    def __init__(
        self,
        repository: TrainingRepository,
        storage: TrainingStorage,
        *,
        popen_factory: Callable[..., ProcessHandle] = subprocess.Popen,
        stop_timeout_seconds: float = 15.0,
        monitor_interval_seconds: float = 0.25,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._popen_factory = popen_factory
        self._stop_timeout = stop_timeout_seconds
        self._monitor_interval = monitor_interval_seconds
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._shutdown = False
        self._active: _ActiveProcess | None = None

    def start(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._repository.recover_interrupted()
            self._shutdown = False
            self._thread = threading.Thread(
                target=self._run,
                name="local-training-manager",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._condition:
            thread = self._thread
            if thread is None:
                return
            self._shutdown = True
            self._condition.notify_all()
        thread.join(timeout=self._stop_timeout + 2.0)
        with self._condition:
            if not thread.is_alive() and self._thread is thread:
                self._thread = None

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                shutting_down = self._shutdown
            if shutting_down:
                self._shutdown_active()
                return

            if self._active is None:
                job = self._repository.claim_next()
                if job is None:
                    with self._condition:
                        if not self._shutdown:
                            self._condition.wait()
                    continue
                self._launch(job)
                continue

            self._monitor_active()
            with self._condition:
                if not self._shutdown and self._active is not None:
                    self._condition.wait(timeout=self._monitor_interval)

    def _launch(self, job: dict[str, object]) -> None:
        job_id = int(job["id"])
        run_key = str(job["run_key"])
        run_dir = self._storage.run_path(run_key)
        manifest = (run_dir / "manifest.json").resolve()
        try:
            manifest.relative_to(run_dir)
            if not manifest.is_file():
                raise FileNotFoundError("training manifest is unavailable")
            log_path = self._storage.resolve_artifact(run_key, "log")
            log = log_path.open("ab", buffering=0)
            try:
                process = self._popen_factory(
                    [
                        sys.executable,
                        "-m",
                        "training.worker",
                        "--manifest",
                        str(manifest),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                )
            except BaseException:
                log.close()
                raise
        except BaseException:
            self._repository.mark_terminal(
                job_id,
                status="failed",
                error_code="training_failed",
                error_message="Training did not produce a valid model.",
            )
            return
        self._active = _ActiveProcess(job_id, run_key, process, log)
        self._repository.update_progress(
            job_id,
            current_epoch=int(job.get("current_epoch", 0)),
            pid=process.pid,
        )

    def _monitor_active(self) -> None:
        active = self._active
        if active is None:
            return
        job = self._repository.get_job(active.job_id)
        if job is None:
            self._terminate(active)
            self._clear_active(active)
            return
        if job["status"] == "stopping" or job["stop_requested"]:
            self._terminate(active)
            self._repository.mark_terminal(
                active.job_id,
                status="stopped",
                has_best=self._artifact_exists(active.run_key, "best"),
                has_last=self._artifact_exists(active.run_key, "last"),
            )
            self._clear_active(active)
            return

        self._ingest_progress(active)
        if active.process.poll() is None:
            return
        self._ingest_terminal_result(active)
        self._clear_active(active)

    def _ingest_progress(self, active: _ActiveProcess) -> None:
        path = self._storage.resolve_artifact(active.run_key, "progress")
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            return
        if payload == active.progress_bytes:
            return
        try:
            progress = TrainingProgress.from_json(json.loads(payload))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return
        active.progress_bytes = payload
        current = self._repository.get_job(active.job_id)
        if current is None or current["status"] == "stopping":
            return
        self._repository.update_progress(
            active.job_id,
            current_epoch=progress.current_epoch,
            metrics=progress.metrics,
            status=(
                "evaluating"
                if progress.current_epoch == progress.total_epochs
                else "running"
            ),
        )

    def _ingest_terminal_result(self, active: _ActiveProcess) -> None:
        try:
            payload = json.loads(
                self._storage.resolve_artifact(active.run_key, "result").read_text(
                    encoding="utf-8"
                )
            )
            result = TrainingResult.from_json(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            self._mark_invalid_result(active.job_id, active.run_key)
            return
        if result.status == "failed":
            self._repository.mark_terminal(
                active.job_id,
                status="failed",
                has_last=self._artifact_exists(active.run_key, "last"),
                error_code=result.error_code,
                error_message=result.error_message,
            )
            return

        best = self._storage.resolve_artifact(active.run_key, "best")
        if not best.is_file() or self._sha256(best) != result.best_sha256:
            self._mark_invalid_result(active.job_id, active.run_key)
            return
        assert result.metrics is not None
        no_helmet = result.metrics.get("classes", {}).get("no_helmet", {})
        stored_metrics = {
            **result.metrics,
            "no_helmet_precision": no_helmet.get("precision"),
            "no_helmet_recall": no_helmet.get("recall"),
            "accepted": result.accepted,
        }
        self._repository.mark_terminal(
            active.job_id,
            status="completed",
            metrics=stored_metrics,
            has_best=True,
            has_last=self._artifact_exists(active.run_key, "last"),
            best_sha256=result.best_sha256,
        )

    def _mark_invalid_result(self, job_id: int, run_key: str) -> None:
        self._repository.mark_terminal(
            job_id,
            status="failed",
            has_last=self._artifact_exists(run_key, "last"),
            error_code="training_failed",
            error_message="Training did not produce a valid model.",
        )

    def _shutdown_active(self) -> None:
        active = self._active
        if active is None:
            return
        if active.process.poll() is None:
            self._terminate(active)
        self._repository.mark_terminal(
            active.job_id,
            status="interrupted",
            has_best=self._artifact_exists(active.run_key, "best"),
            has_last=self._artifact_exists(active.run_key, "last"),
        )
        self._clear_active(active)

    def _terminate(self, active: _ActiveProcess) -> None:
        if active.process.poll() is not None:
            return
        active.process.terminate()
        try:
            active.process.wait(timeout=self._stop_timeout)
        except subprocess.TimeoutExpired:
            active.process.kill()
            active.process.wait(timeout=self._stop_timeout)

    def _clear_active(self, active: _ActiveProcess) -> None:
        active.log.close()
        if self._active is active:
            self._active = None

    def _artifact_exists(self, run_key: str, kind: str) -> bool:
        try:
            return self._storage.resolve_artifact(run_key, kind).is_file()
        except ValueError:
            return False

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
