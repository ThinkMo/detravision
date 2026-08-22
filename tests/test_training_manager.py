from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import pytest

from backend.core.config import Settings
from backend.db.migrate import run_migrations
from backend.db.models import User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.training.manager import LocalTrainingManager
from backend.training.repository import TrainingRepository
from backend.training.storage import TrainingStorage


def wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


@pytest.fixture
def manager_context(tmp_path: Path):
    database_url = "sqlite:///" + (tmp_path / "training.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        admin = User(
            username="admin",
            password_hash="unused",
            role="admin",
            enabled=True,
            must_change_password=False,
        )
        session.add(admin)
        session.flush()
        admin_id = admin.id
    settings = Settings(
        database_url=database_url,
        training_dataset_yaml=tmp_path / "dataset.yaml",
        training_run_dir=tmp_path / "runs",
        training_model_cache_dir=tmp_path / "models",
        training_stop_timeout_seconds=0.02,
    )
    settings.training_dataset_yaml.write_text(
        "train: train\nval: val\ntest: test\nnames: [person, helmet, no_helmet]\n",
        encoding="utf-8",
    )
    repository = TrainingRepository(session_factory)
    storage = TrainingStorage(settings)
    try:
        yield repository, storage, admin_id
    finally:
        engine.dispose()


def queued_job(repository, storage, admin_id: int, suffix: str):
    run_key = storage.create_run()
    run_dir = storage.run_path(run_key)
    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return repository.create_job(
        {
            "created_by": admin_id,
            "executor": "local",
            "base_model": "yolo11n.pt",
            "dataset_key": "configured",
            "epochs": 3,
            "imgsz": 640,
            "batch": "4",
            "device": "cpu",
            "patience": 2,
            "run_key": run_key,
            "idempotency_key": f"manager-{suffix}",
        }
    )


class FakeProcess:
    next_pid = 4100

    def __init__(self, *, needs_kill: bool = False):
        self.pid = self.__class__.next_pid
        self.__class__.next_pid += 1
        self.returncode: int | None = None
        self.needs_kill = needs_kill
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        if self.needs_kill and self.terminated and not self.killed:
            raise subprocess.TimeoutExpired("training.worker", timeout)
        if self.terminated or self.killed:
            self.returncode = -9 if self.killed else -15
            return self.returncode
        raise subprocess.TimeoutExpired("training.worker", timeout)

    def terminate(self):
        self.terminated = True
        if not self.needs_kill:
            self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def finish(self, returncode: int = 0):
        self.returncode = returncode


class PopenRecorder:
    def __init__(self, processes: list[FakeProcess] | None = None):
        self.processes = list(processes or [])
        self.calls: list[tuple[list[str], dict[str, object], FakeProcess]] = []

    def __call__(self, args, **kwargs):
        process = self.processes.pop(0) if self.processes else FakeProcess()
        self.calls.append((list(args), kwargs, process))
        return process


class FaultyStopProcess(FakeProcess):
    def __init__(self, stage: str):
        super().__init__(needs_kill=True)
        self.stage = stage
        self.wait_calls = 0

    def terminate(self):
        self.terminated = True
        if self.stage == "terminate_error":
            raise OSError("terminate failed")

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.stage == "double_timeout":
            raise subprocess.TimeoutExpired("training.worker", timeout)
        if self.returncode is not None:
            return self.returncode
        if self.stage == "wait_error" and self.wait_calls == 1:
            raise OSError("wait failed")
        if not self.killed:
            raise subprocess.TimeoutExpired("training.worker", timeout)
        return self.returncode

    def kill(self):
        if self.stage == "kill_error":
            raise OSError("kill failed")
        super().kill()


def completed_result() -> dict[str, object]:
    return {
        "status": "completed",
        "metrics": {
            "precision": 0.87,
            "recall": 0.91,
            "map50": 0.90,
            "map50_95": 0.66,
            "classes": {
                "no_helmet": {
                    "precision": 0.86,
                    "recall": 0.92,
                    "map50": 0.88,
                    "map50_95": 0.61,
                }
            },
        },
        "accepted": True,
        "best_sha256": "8f4375ec65c2c4092a1cc21e76712472eae0de999384e8327c495957121db9c2",
        "error_code": None,
        "error_message": None,
    }


def test_manager_runs_one_process_and_ingests_progress_then_result(manager_context):
    """Catches concurrent launches or lost worker artifacts in scheduler handoff."""
    repository, storage, admin_id = manager_context
    first = queued_job(repository, storage, admin_id, "first")
    second = queued_job(repository, storage, admin_id, "second")
    first_process, second_process = FakeProcess(), FakeProcess()
    popen = PopenRecorder([first_process, second_process])
    manager = LocalTrainingManager(
        repository,
        storage,
        popen_factory=popen,
        stop_timeout_seconds=0.02,
    )

    manager.start()
    try:
        wait_until(lambda: len(popen.calls) == 1)
        args, kwargs, _ = popen.calls[0]
        assert args == [
            sys.executable,
            "-m",
            "training.worker",
            "--manifest",
            str(storage.run_path(first["run_key"]) / "manifest.json"),
        ]
        assert kwargs["shell"] is False
        assert kwargs["cwd"] == str(Path(__file__).resolve().parents[1])
        assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
        assert "API_TOKEN" not in kwargs["env"]
        assert kwargs["stderr"] is subprocess.STDOUT
        assert kwargs["stdout"].name == str(storage.resolve_artifact(first["run_key"], "log"))
        assert repository.get_job(first["id"])["pid"] == first_process.pid
        assert repository.get_job(second["id"])["status"] == "queued"

        storage.resolve_artifact(first["run_key"], "progress").write_text(
            json.dumps(
                {"current_epoch": 2, "total_epochs": 3, "metrics": {"map50": 0.4}}
            ),
            encoding="utf-8",
        )
        manager.wake()
        wait_until(lambda: repository.get_job(first["id"])["current_epoch"] == 2)

        weights = storage.resolve_artifact(first["run_key"], "best").parent
        weights.mkdir(parents=True)
        storage.resolve_artifact(first["run_key"], "best").write_bytes(b"best")
        storage.resolve_artifact(first["run_key"], "last").write_bytes(b"last")
        storage.resolve_artifact(first["run_key"], "result").write_text(
            json.dumps(completed_result()), encoding="utf-8"
        )
        first_process.finish()
        manager.wake()

        wait_until(lambda: repository.get_job(first["id"])["status"] == "completed")
        wait_until(lambda: len(popen.calls) == 2)
        completed = repository.get_job(first["id"])
        assert completed["has_best"] is True
        assert completed["has_last"] is True
        assert completed["best_sha256"] == (
            "8f4375ec65c2c4092a1cc21e76712472eae0de999384e8327c495957121db9c2"
        )
        assert completed["no_helmet_recall"] == 0.92
        assert repository.get_job(second["id"])["status"] == "running"
    finally:
        second_process.finish(1)
        manager.wake()
        manager.stop()


def test_requested_stop_escalates_only_the_owned_live_process(manager_context):
    """Catches PID-based or unrelated process termination during stop escalation."""
    repository, storage, admin_id = manager_context
    job = queued_job(repository, storage, admin_id, "stop")
    owned = FakeProcess(needs_kill=True)
    unrelated = FakeProcess()
    popen = PopenRecorder([owned])
    manager = LocalTrainingManager(
        repository,
        storage,
        popen_factory=popen,
        stop_timeout_seconds=0.01,
    )
    manager.start()
    try:
        wait_until(lambda: repository.get_job(job["id"])["status"] == "running")
        repository.request_stop(job["id"])
        manager.wake()
        wait_until(lambda: repository.get_job(job["id"])["status"] == "stopped")
        assert (owned.terminated, owned.killed) == (True, True)
        assert (unrelated.terminated, unrelated.killed) == (False, False)
    finally:
        manager.stop()


def test_idle_manager_waits_for_wake_and_shutdown_returns_promptly(manager_context):
    """Catches an empty queue spinning or shutdown sleeping through a condition wait."""
    repository, storage, _admin_id = manager_context

    class CountingRepository:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.claims = 0

        def claim_next(self):
            self.claims += 1
            return self.wrapped.claim_next()

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    counting = CountingRepository(repository)
    manager = LocalTrainingManager(counting, storage, popen_factory=PopenRecorder())
    manager.start()
    time.sleep(0.08)
    started = time.monotonic()
    manager.stop()

    assert counting.claims <= 2
    assert time.monotonic() - started < 0.5


def test_startup_interrupts_stale_active_job_without_signaling_stored_pid(manager_context):
    """Catches a reused persisted PID being treated as an owned child after restart."""
    repository, storage, admin_id = manager_context
    job = queued_job(repository, storage, admin_id, "restart")
    running = repository.claim_next()
    repository.update_progress(running["id"], current_epoch=1, pid=999999)
    popen = PopenRecorder()
    manager = LocalTrainingManager(repository, storage, popen_factory=popen)

    manager.start()
    try:
        wait_until(lambda: repository.get_job(job["id"])["status"] == "interrupted")
        assert popen.calls == []
    finally:
        manager.stop()


def test_shutdown_gracefully_terminates_owned_child_and_marks_interrupted(manager_context):
    """Catches application shutdown abandoning a child or sleeping indefinitely."""
    repository, storage, admin_id = manager_context
    job = queued_job(repository, storage, admin_id, "shutdown")
    process = FakeProcess()
    manager = LocalTrainingManager(
        repository,
        storage,
        popen_factory=PopenRecorder([process]),
        stop_timeout_seconds=0.02,
    )
    manager.start()
    wait_until(lambda: repository.get_job(job["id"])["status"] == "running")

    started = time.monotonic()
    stopped = manager.stop()

    assert time.monotonic() - started < 0.5
    assert process.terminated is True
    assert process.killed is False
    assert repository.get_job(job["id"])["status"] == "interrupted"
    assert stopped is True


def test_wake_between_empty_claim_and_condition_wait_is_not_lost(manager_context):
    """Catches a queued job sleeping forever when wake races with the idle wait."""
    repository, storage, admin_id = manager_context
    job = queued_job(repository, storage, admin_id, "lost-wake")
    entered_claim = Event()
    release_claim = Event()

    class RacingRepository:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.first = True

        def claim_next(self):
            if self.first:
                self.first = False
                entered_claim.set()
                assert release_claim.wait(timeout=1)
                return None
            return self.wrapped.claim_next()

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    process = FakeProcess()
    popen = PopenRecorder([process])
    manager = LocalTrainingManager(RacingRepository(repository), storage, popen_factory=popen)
    manager.start()
    assert entered_claim.wait(timeout=1)
    manager.wake()
    release_claim.set()
    try:
        wait_until(lambda: repository.get_job(job["id"])["status"] == "running")
        wait_until(lambda: len(popen.calls) == 1)
    finally:
        process.finish(1)
        manager.wake()
        manager.stop()


def test_only_one_manager_owns_run_root_and_second_takes_over_after_release(
    manager_context,
):
    """Catches two service processes recovering or claiming the same local GPU queue."""
    repository, storage, admin_id = manager_context
    first_job = queued_job(repository, storage, admin_id, "lock-first")
    first_process = FakeProcess()
    first_popen = PopenRecorder([first_process])
    second_popen = PopenRecorder()
    first = LocalTrainingManager(repository, storage, popen_factory=first_popen)
    second = LocalTrainingManager(repository, storage, popen_factory=second_popen)

    assert first.start() is True
    wait_until(lambda: repository.get_job(first_job["id"])["status"] == "running")
    assert second.start() is False
    assert repository.get_job(first_job["id"])["status"] == "running"
    assert second_popen.calls == []

    assert first.stop() is True
    next_job = queued_job(repository, storage, admin_id, "lock-next")
    takeover_process = FakeProcess()
    second_popen.processes.append(takeover_process)
    assert second.start() is True
    try:
        wait_until(lambda: repository.get_job(next_job["id"])["status"] == "running")
        assert len(second_popen.calls) == 1
    finally:
        takeover_process.finish(1)
        second.wake()
        second.stop()


def test_worker_launch_uses_project_root_even_when_service_cwd_changes(
    manager_context, tmp_path: Path, monkeypatch
):
    """Catches `python -m training.worker` depending on the web service cwd."""
    repository, storage, admin_id = manager_context
    job = queued_job(repository, storage, admin_id, "cwd")
    process = FakeProcess()
    popen = PopenRecorder([process])
    monkeypatch.setenv("API_TOKEN", "must-not-reach-child")
    monkeypatch.chdir(tmp_path)
    manager = LocalTrainingManager(repository, storage, popen_factory=popen)

    manager.start()
    try:
        wait_until(lambda: repository.get_job(job["id"])["status"] == "running")
        _args, kwargs, _process = popen.calls[0]
        assert kwargs["cwd"] == str(Path(__file__).resolve().parents[1])
        assert "API_TOKEN" not in kwargs["env"]
        assert kwargs["env"].get("PATH") == os.environ.get("PATH")
    finally:
        process.finish(1)
        manager.wake()
        manager.stop()


@pytest.mark.parametrize("artifact", ["progress", "result"])
def test_manager_bounds_structured_artifacts_to_one_megabyte(
    manager_context, artifact: str
):
    """Catches an untrusted worker artifact causing an unbounded manager read."""
    repository, storage, admin_id = manager_context
    job = queued_job(repository, storage, admin_id, f"oversized-{artifact}")
    process = FakeProcess()
    manager = LocalTrainingManager(
        repository, storage, popen_factory=PopenRecorder([process])
    )
    manager.start()
    try:
        wait_until(lambda: repository.get_job(job["id"])["status"] == "running")
        if artifact == "progress":
            valid = json.dumps(
                {"current_epoch": 3, "total_epochs": 3, "metrics": {}}
            ).encode("utf-8")
        else:
            weights = storage.resolve_artifact(job["run_key"], "best").parent
            weights.mkdir(parents=True)
            storage.resolve_artifact(job["run_key"], "best").write_bytes(b"best")
            valid = json.dumps(completed_result()).encode("utf-8")
        storage.resolve_artifact(job["run_key"], artifact).write_bytes(
            valid + b" " * (1024 * 1024)
        )
        if artifact == "result":
            process.finish(0)
        manager.wake()
        if artifact == "result":
            wait_until(lambda: repository.get_job(job["id"])["status"] == "failed")
        else:
            time.sleep(0.05)
            assert repository.get_job(job["id"])["current_epoch"] == 0
    finally:
        process.finish(1)
        manager.wake()
        manager.stop()


@pytest.mark.parametrize("stage", ["get_job", "progress_path"])
def test_transient_monitor_exceptions_do_not_kill_scheduler(
    manager_context, stage: str
):
    """Catches one repository/storage failure permanently abandoning the queue."""
    repository, storage, admin_id = manager_context
    job = queued_job(repository, storage, admin_id, f"transient-{stage}")
    process = FakeProcess()
    popen = PopenRecorder([process])
    if stage == "get_job":
        original = repository.get_job
        failed = False

        def flaky_get(job_id):
            nonlocal failed
            if popen.calls and not failed:
                failed = True
                raise RuntimeError("temporary database failure")
            return original(job_id)

        repository.get_job = flaky_get
    else:
        original = storage.resolve_artifact
        failed = False

        def flaky_resolve(run_key, kind):
            nonlocal failed
            if kind == "progress" and not failed:
                failed = True
                raise OSError("temporary storage failure")
            return original(run_key, kind)

        storage.resolve_artifact = flaky_resolve
    manager = LocalTrainingManager(repository, storage, popen_factory=popen)
    manager.start()
    try:
        wait_until(lambda: len(popen.calls) == 1)
        manager.wake()
        time.sleep(0.05)
        repository.request_stop(job["id"])
        manager.wake()
        wait_until(lambda: repository.get_job(job["id"])["status"] == "stopped")
    finally:
        manager.stop()


def test_launch_persistence_failure_terminates_owned_child_and_continues_queue(
    manager_context,
):
    """Catches a PID persistence error leaking a child and killing the scheduler."""
    repository, storage, admin_id = manager_context
    first = queued_job(repository, storage, admin_id, "persist-fail")
    second = queued_job(repository, storage, admin_id, "persist-next")
    original_update = repository.update_progress
    failed = False

    def flaky_update(*args, **kwargs):
        nonlocal failed
        if kwargs.get("pid") is not None and not failed:
            failed = True
            raise RuntimeError("pid persistence failed")
        return original_update(*args, **kwargs)

    repository.update_progress = flaky_update
    first_process, second_process = FakeProcess(), FakeProcess()
    popen = PopenRecorder([first_process, second_process])
    manager = LocalTrainingManager(repository, storage, popen_factory=popen)
    manager.start()
    try:
        wait_until(lambda: repository.get_job(first["id"])["status"] == "failed")
        wait_until(lambda: repository.get_job(second["id"])["status"] == "running")
        assert first_process.terminated is True
    finally:
        second_process.finish(1)
        manager.wake()
        manager.stop()


def test_stop_returns_false_when_scheduler_cannot_join(manager_context):
    """Catches shutdown silently claiming success while claim_next is still blocked."""
    repository, storage, _admin_id = manager_context
    entered = Event()
    release = Event()

    class BlockingRepository:
        def claim_next(self):
            entered.set()
            release.wait(timeout=2)
            return None

        def recover_interrupted(self):
            return 0

    manager = LocalTrainingManager(
        BlockingRepository(),
        storage,
        popen_factory=PopenRecorder(),
        stop_timeout_seconds=0.01,
        join_timeout_seconds=0.02,
    )
    manager.start()
    assert entered.wait(timeout=1)
    try:
        assert manager.stop() is False
    finally:
        release.set()
        assert manager.stop() is True


@pytest.mark.parametrize(
    ("stage", "terminal_status"),
    [
        ("terminate_error", "stopped"),
        ("wait_error", "stopped"),
        ("kill_error", "interrupted"),
        ("double_timeout", "interrupted"),
    ],
)
def test_stop_exceptions_keep_scheduler_alive_and_close_owned_log(
    manager_context, stage: str, terminal_status: str
):
    """Catches terminate/kill/wait failures escaping the scheduler lifecycle."""
    repository, storage, admin_id = manager_context
    job = queued_job(repository, storage, admin_id, f"stop-{stage}")
    process = FaultyStopProcess(stage)
    popen = PopenRecorder([process])
    manager = LocalTrainingManager(
        repository,
        storage,
        popen_factory=popen,
        stop_timeout_seconds=0.01,
    )
    manager.start()
    try:
        wait_until(lambda: repository.get_job(job["id"])["status"] == "running")
        repository.request_stop(job["id"])
        manager.wake()
        wait_until(
            lambda: repository.get_job(job["id"])["status"] == terminal_status
        )
        assert popen.calls[0][1]["stdout"].closed is True
    finally:
        assert manager.stop() is True


@pytest.mark.parametrize(
    "payload",
    [
        {"current_epoch": 3, "total_epochs": 999, "metrics": {"map50": 0.4}},
        {"current_epoch": 1, "total_epochs": 3, "metrics": {"map50": 0.4}},
    ],
)
def test_progress_cannot_forge_total_epochs_or_move_backwards(
    manager_context, payload: dict[str, object]
):
    """Catches forged evaluation status or stale progress overwriting newer state."""
    repository, storage, admin_id = manager_context
    job = queued_job(repository, storage, admin_id, "progress-integrity")
    process = FakeProcess()
    manager = LocalTrainingManager(
        repository, storage, popen_factory=PopenRecorder([process])
    )
    manager.start()
    try:
        wait_until(lambda: repository.get_job(job["id"])["status"] == "running")
        repository.update_progress(job["id"], current_epoch=2)
        storage.resolve_artifact(job["run_key"], "progress").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        manager.wake()
        time.sleep(0.05)
        stored = repository.get_job(job["id"])
        assert stored["current_epoch"] == 2
        assert stored["status"] == "running"
    finally:
        process.finish(1)
        manager.wake()
        manager.stop()
