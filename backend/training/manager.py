from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Protocol

from training.contracts import TrainingProgress, TrainingResult

from .repository import TrainingRepository
from .storage import TrainingStorage


MAX_STRUCTURED_ARTIFACT_BYTES = 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CHILD_ENV_KEYS = (
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "VIRTUAL_ENV",
    "CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES",
)


class ProcessHandle(Protocol):
    pid: int
    returncode: int | None
    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


class _LocalManagerLock:
    """A non-blocking same-host lock held for one scheduler lifetime."""

    def __init__(self, root: Path) -> None:
        self._path = root / ".training-manager.lock"
        self._stream: BinaryIO | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        stream = self._path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            stream.close()
            return False
        self._stream = stream
        return True

    def release(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


@dataclass(slots=True)
class _ActiveProcess:
    job_id: int
    run_key: str
    process: ProcessHandle
    log: BinaryIO
    progress_bytes: bytes | None = None


class LocalTrainingManager:
    """Own the same-host training lock and serialize one local child process."""

    def __init__(
        self,
        repository: TrainingRepository,
        storage: TrainingStorage,
        *,
        popen_factory: Callable[..., ProcessHandle] = subprocess.Popen,
        stop_timeout_seconds: float = 15.0,
        monitor_interval_seconds: float = 0.25,
        join_timeout_seconds: float | None = None,
        project_root: Path = PROJECT_ROOT,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._popen_factory = popen_factory
        self._stop_timeout = stop_timeout_seconds
        self._monitor_interval = monitor_interval_seconds
        self._join_timeout = stop_timeout_seconds + 2.0 if join_timeout_seconds is None else join_timeout_seconds
        self._project_root = project_root.resolve()
        self._condition = threading.Condition()
        self._generation = 0
        self._thread: threading.Thread | None = None
        self._shutdown = False
        self._active: _ActiveProcess | None = None
        self._quarantined = False
        self._quarantine_terminal: dict[str, object] | None = None
        self._local_lock = _LocalManagerLock(storage.root)

    def start(self) -> bool:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return True
            if not self._local_lock.acquire():
                return False
            try:
                self._repository.recover_interrupted()
            except Exception:
                self._local_lock.release()
                return False
            self._shutdown = False
            self._generation += 1
            self._thread = threading.Thread(target=self._thread_main, name="local-training-manager", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._condition:
            thread = self._thread
            if thread is None:
                return True
            self._shutdown = True
            self._generation += 1
            self._condition.notify_all()
        thread.join(timeout=self._join_timeout)
        if thread.is_alive():
            return False
        with self._condition:
            if self._thread is thread:
                self._thread = None
        return True

    def wake(self) -> None:
        with self._condition:
            self._generation += 1
            self._condition.notify_all()

    def owns_job(self, job_id: int) -> bool:
        with self._condition:
            return self._active is not None and self._active.job_id == job_id

    def _thread_main(self) -> None:
        try:
            self._run()
        finally:
            if self._active is None:
                self._local_lock.release()
            with self._condition:
                self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                shutting_down = self._shutdown
                observed_generation = self._generation
            if shutting_down:
                if self._active is None:
                    return
                self._monitor_shutdown()
                self._wait_for_change(observed_generation, bounded=True)
                continue
            if self._active is None:
                try:
                    job = self._repository.claim_next()
                except Exception:
                    self._wait_for_change(observed_generation, bounded=True)
                    continue
                if job is None:
                    self._wait_for_change(observed_generation, bounded=False)
                    continue
                self._launch(job)
                continue
            try:
                self._monitor_active()
            except Exception:
                pass
            self._wait_for_change(observed_generation, bounded=True)

    def _wait_for_change(self, observed_generation: int, *, bounded: bool) -> None:
        with self._condition:
            if self._generation != observed_generation:
                return
            if self._shutdown and not bounded:
                return
            self._condition.wait(timeout=self._monitor_interval if bounded else None)

    def _launch(self, job: dict[str, object]) -> None:
        job_id, run_key = int(job["id"]), str(job["run_key"])
        log: BinaryIO | None = None
        process: ProcessHandle | None = None
        try:
            run_dir = self._storage.run_path(run_key)
            manifest = (run_dir / "manifest.json").resolve()
            manifest.relative_to(run_dir)
            if not manifest.is_file():
                raise FileNotFoundError("training manifest is unavailable")
            log = self._storage.resolve_artifact(run_key, "log").open("ab", buffering=0)
            process = self._popen_factory(
                [sys.executable, "-m", "training.worker", "--manifest", str(manifest)],
                stdout=log, stderr=subprocess.STDOUT, shell=False,
                cwd=str(self._project_root), env=self._child_environment(),
            )
            active = _ActiveProcess(job_id, run_key, process, log)
            self._active = active
            self._quarantined = False
            self._repository.update_progress(job_id, current_epoch=int(job.get("current_epoch", 0)), pid=process.pid)
        except BaseException:
            if process is not None and log is not None:
                active = self._active or _ActiveProcess(job_id, run_key, process, log)
                self._active = active
                if not self._terminate(active):
                    self._quarantine_active(
                        active,
                        status="failed",
                        error_code="training_failed",
                        error_message="Training did not produce a valid model.",
                    )
                    return
            if log is not None:
                self._close_log(log)
            self._active = None
            self._safe_mark_terminal(
                job_id, status="failed", error_code="training_failed",
                error_message="Training did not produce a valid model.",
            )

    def _monitor_active(self) -> None:
        active = self._active
        if active is None:
            return
        if self._quarantined:
            self._finalize_quarantined(active)
            return
        job = self._repository.get_job(active.job_id)
        if job is None:
            terminal = {
                "status": "interrupted",
                "has_best": self._artifact_exists(active.run_key, "best"),
                "has_last": self._artifact_exists(active.run_key, "last"),
            }
            if not self._terminate(active):
                self._quarantine_active(active, **terminal)
                return
            if self._safe_mark_terminal(active.job_id, **terminal):
                self._clear_active(active)
            return
        if job["status"] == "stopping" or job["stop_requested"]:
            stopped = self._terminate(active)
            if not stopped:
                self._quarantine_active(
                    active,
                    status="interrupted",
                    has_best=self._artifact_exists(active.run_key, "best"),
                    has_last=self._artifact_exists(active.run_key, "last"),
                )
                return
            finalized = self._safe_mark_terminal(
                active.job_id, status="stopped",
                has_best=self._artifact_exists(active.run_key, "best"),
                has_last=self._artifact_exists(active.run_key, "last"),
            )
            if finalized:
                self._clear_active(active)
            return
        self._ingest_progress(active, job)
        if active.process.poll() is None:
            return
        if self._ingest_terminal_result(active):
            self._clear_active(active)

    def _ingest_progress(self, active: _ActiveProcess, job: dict[str, object]) -> None:
        try:
            payload = self._read_structured_artifact(active.run_key, "progress")
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            return
        if payload == active.progress_bytes:
            return
        try:
            progress = TrainingProgress.from_json(json.loads(payload))
        except (UnicodeError, json.JSONDecodeError, ValueError):
            return
        active.progress_bytes = payload
        if progress.total_epochs != int(job["total_epochs"]) or progress.current_epoch < int(job["current_epoch"]):
            return
        self._repository.update_progress(
            active.job_id, current_epoch=progress.current_epoch, metrics=progress.metrics,
            status="evaluating" if progress.current_epoch == progress.total_epochs else "running",
        )

    def _ingest_terminal_result(self, active: _ActiveProcess) -> bool:
        try:
            result = TrainingResult.from_json(json.loads(self._read_structured_artifact(active.run_key, "result")))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return self._mark_invalid_result(active.job_id, active.run_key)
        if result.status == "failed":
            return self._safe_mark_terminal(
                active.job_id, status="failed", has_last=self._artifact_exists(active.run_key, "last"),
                error_code=result.error_code, error_message=result.error_message,
            )
        try:
            best = self._storage.resolve_artifact(active.run_key, "best")
            if not best.is_file() or self._sha256(best) != result.best_sha256:
                return self._mark_invalid_result(active.job_id, active.run_key)
        except (OSError, ValueError):
            return self._mark_invalid_result(active.job_id, active.run_key)
        assert result.metrics is not None
        no_helmet = result.metrics.get("classes", {}).get("no_helmet", {})
        stored_metrics = {
            **result.metrics, "no_helmet_precision": no_helmet.get("precision"),
            "no_helmet_recall": no_helmet.get("recall"), "accepted": result.accepted,
        }
        return self._safe_mark_terminal(
            active.job_id, status="completed", metrics=stored_metrics, has_best=True,
            has_last=self._artifact_exists(active.run_key, "last"), best_sha256=result.best_sha256,
        )

    def _mark_invalid_result(self, job_id: int, run_key: str) -> bool:
        active = self._active
        exit_code = active.process.poll() if active is not None else None
        self._storage.append_safe_diagnostic(
            run_key, "result", "worker_exit", exit_code=exit_code
        )
        return self._safe_mark_terminal(
            job_id, status="failed", has_last=self._artifact_exists(run_key, "last"),
            error_code="training_failed", error_message="Training did not produce a valid model.",
        )

    def _safe_mark_terminal(self, job_id: int, **values: object) -> bool:
        try:
            return self._repository.mark_terminal(job_id, **values) is not None
        except Exception:
            return False

    def _monitor_shutdown(self) -> None:
        active = self._active
        if active is None:
            return
        if self._quarantined:
            self._finalize_quarantined(active)
            return
        if not self._terminate(active):
            self._quarantine_active(
                active,
                status="interrupted",
                has_best=self._artifact_exists(active.run_key, "best"),
                has_last=self._artifact_exists(active.run_key, "last"),
            )
            return
        if not self._safe_mark_terminal(
            active.job_id, status="interrupted",
            has_best=self._artifact_exists(active.run_key, "best"),
            has_last=self._artifact_exists(active.run_key, "last"),
        ):
            return
        self._clear_active(active)

    def _quarantine_active(self, active: _ActiveProcess, **terminal: object) -> None:
        """Retain sole ownership until the child is observably terminated."""
        self._active = active
        self._quarantined = True
        self._quarantine_terminal = terminal

    def _finalize_quarantined(self, active: _ActiveProcess) -> bool:
        if not self._process_exited(active):
            return False
        terminal = self._quarantine_terminal
        if terminal is None or not self._safe_mark_terminal(active.job_id, **terminal):
            return False
        self._clear_active(active)
        return True

    @staticmethod
    def _process_exited(active: _ActiveProcess) -> bool:
        try:
            return active.process.poll() is not None
        except Exception:
            return False

    def _terminate(self, active: _ActiveProcess) -> bool:
        try:
            if active.process.poll() is not None:
                return True
        except Exception:
            pass
        try:
            active.process.terminate()
        except Exception:
            pass
        try:
            active.process.wait(timeout=self._stop_timeout)
            return True
        except Exception:
            pass
        try:
            active.process.kill()
        except Exception:
            return False
        try:
            active.process.wait(timeout=self._stop_timeout)
            return True
        except Exception:
            return False

    def _clear_active(self, active: _ActiveProcess) -> None:
        self._close_log(active.log)
        if self._active is active:
            self._active = None
        self._quarantined = False
        self._quarantine_terminal = None
        self.wake()

    @staticmethod
    def _close_log(log: BinaryIO) -> None:
        try:
            log.close()
        except Exception:
            pass

    def _artifact_exists(self, run_key: str, kind: str) -> bool:
        try:
            return self._storage.resolve_artifact(run_key, kind).is_file()
        except (OSError, ValueError):
            return False

    def _read_structured_artifact(self, run_key: str, kind: str) -> bytes:
        path = self._storage.resolve_artifact(run_key, kind)
        if path.stat().st_size > MAX_STRUCTURED_ARTIFACT_BYTES:
            raise ValueError("training artifact exceeds size limit")
        with path.open("rb") as stream:
            payload = stream.read(MAX_STRUCTURED_ARTIFACT_BYTES + 1)
        if len(payload) > MAX_STRUCTURED_ARTIFACT_BYTES:
            raise ValueError("training artifact exceeds size limit")
        return payload

    @staticmethod
    def _child_environment() -> dict[str, str]:
        environment = {key: value for key in _CHILD_ENV_KEYS if (value := os.environ.get(key))}
        environment.update(
            PYTHONIOENCODING="utf-8",
            PYTHONUNBUFFERED="1",
            USER="training-worker",
        )
        return environment

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
