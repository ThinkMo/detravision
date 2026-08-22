from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.db.migrate import run_migrations
from backend.db.models import TrainingJob, User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.training.repository import TrainingRepository


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def training_context(tmp_path):
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
    repository = TrainingRepository(session_factory, clock=lambda: NOW)
    try:
        yield repository, session_factory, admin_id
    finally:
        engine.dispose()


def valid_job(*, created_by: int, suffix: str = "1") -> dict[str, object]:
    return {
        "created_by": created_by,
        "executor": "local",
        "base_model": "yolo11n.pt",
        "dataset_key": "safety",
        "epochs": 3,
        "imgsz": 640,
        "batch": "4",
        "device": "0",
        "patience": 2,
        "run_key": f"run-{suffix}",
        "idempotency_key": f"request-{suffix}",
    }


def test_create_job_persists_safe_json_ready_record(training_context):
    """Catches losing configuration or leaking an internal filesystem path."""
    repository, _factory, admin_id = training_context

    job = repository.create_job(valid_job(created_by=admin_id))

    assert (
        job["created_by"],
        job["status"],
        job["base_model"],
        job["total_epochs"],
        job["created_at"],
    ) == (admin_id, "queued", "yolo11n.pt", 3, NOW)
    assert job["metrics"] is None
    assert "path" not in " ".join(job.keys())
    assert repository.get_job(job["id"]) == job


def test_create_job_reuses_an_idempotency_key(training_context):
    """Catches a retried submission creating duplicate training work."""
    repository, _factory, admin_id = training_context
    request = valid_job(created_by=admin_id)

    first = repository.create_job(request)
    second = repository.create_job(request)

    assert second["id"] == first["id"]
    assert repository.list_jobs()["total"] == 1


def test_claim_next_serializes_jobs_in_creation_order(training_context):
    """Catches two local GPU jobs being claimed concurrently or out of order."""
    repository, _factory, admin_id = training_context
    first = repository.create_job(valid_job(created_by=admin_id, suffix="1"))
    second = repository.create_job(valid_job(created_by=admin_id, suffix="2"))

    claimed = repository.claim_next()

    assert claimed is not None
    assert (claimed["id"], claimed["status"], claimed["started_at"]) == (
        first["id"],
        "running",
        NOW,
    )
    assert repository.claim_next() is None
    repository.mark_terminal(first["id"], status="completed", metrics={"map50": 0.9})
    assert repository.claim_next()["id"] == second["id"]


def test_request_stop_cancels_queued_and_is_idempotent(training_context):
    """Catches a cancelled queued job starting later or repeated stop changing state."""
    repository, _factory, admin_id = training_context
    job = repository.create_job(valid_job(created_by=admin_id))

    stopped = repository.request_stop(job["id"])
    repeated = repository.request_stop(job["id"])

    assert (stopped["status"], stopped["stop_requested"], stopped["finished_at"]) == (
        "cancelled",
        True,
        NOW,
    )
    assert repeated == stopped
    assert repository.claim_next() is None


def test_request_stop_marks_a_running_job_as_stopping(training_context):
    """Catches stop requests terminating state without letting the manager own shutdown."""
    repository, _factory, admin_id = training_context
    repository.create_job(valid_job(created_by=admin_id))
    running = repository.claim_next()
    assert running is not None

    stopping = repository.request_stop(running["id"])

    assert stopping["status"] == "stopping"
    assert stopping["stop_requested"] is True
    assert stopping["finished_at"] is None


def test_recover_interrupted_only_changes_active_jobs_once(training_context):
    """Catches restart recovery altering terminal jobs or repeatedly reporting rows."""
    repository, _factory, admin_id = training_context
    active = repository.create_job(valid_job(created_by=admin_id, suffix="active"))
    repository.claim_next()
    terminal = repository.create_job(valid_job(created_by=admin_id, suffix="terminal"))
    repository.request_stop(terminal["id"])

    assert repository.recover_interrupted() == 1
    assert repository.recover_interrupted() == 0
    assert repository.get_job(active["id"])["status"] == "interrupted"
    assert repository.get_job(terminal["id"])["status"] == "cancelled"


def test_resume_copies_configuration_and_records_lineage(training_context):
    """Catches resume mutating history or losing the original training configuration."""
    repository, _factory, admin_id = training_context
    original = repository.create_job(valid_job(created_by=admin_id, suffix="original"))
    repository.claim_next()
    repository.mark_terminal(original["id"], status="stopped", has_last=True)

    resumed = repository.create_resume(
        original["id"], run_key="run-resume", idempotency_key="request-resume"
    )

    assert resumed["id"] != original["id"]
    assert resumed["resume_from_id"] == original["id"]
    assert resumed["status"] == "queued"
    assert resumed["base_model"] == original["base_model"]
    assert repository.get_job(original["id"])["status"] == "stopped"


def test_resume_rejects_a_job_without_last_checkpoint(training_context):
    """Catches creating a resume job that cannot provide last.pt."""
    repository, _factory, admin_id = training_context
    original = repository.create_job(valid_job(created_by=admin_id))
    repository.request_stop(original["id"])

    with pytest.raises(ValueError, match="last checkpoint"):
        repository.create_resume(
            original["id"], run_key="run-resume", idempotency_key="request-resume"
        )

    assert repository.list_jobs()["total"] == 1


def test_resume_rejects_a_completed_job(training_context):
    """Catches resume being offered for a successful immutable run."""
    repository, _factory, admin_id = training_context
    original = repository.create_job(valid_job(created_by=admin_id))
    repository.claim_next()
    repository.mark_terminal(original["id"], status="completed", has_last=True)

    with pytest.raises(ValueError, match="cannot be resumed"):
        repository.create_resume(
            original["id"], run_key="run-resume", idempotency_key="request-resume"
        )


def test_list_jobs_is_newest_first_and_page_size_is_bounded(training_context):
    """Catches unstable history paging or unbounded database reads."""
    repository, _factory, admin_id = training_context
    first = repository.create_job(valid_job(created_by=admin_id, suffix="1"))
    second = repository.create_job(valid_job(created_by=admin_id, suffix="2"))

    page = repository.list_jobs(page=1, page_size=1)

    assert page == {"items": [second], "total": 2, "page": 1, "page_size": 1}
    assert first["id"] != second["id"]
    with pytest.raises(ValueError, match="between 1 and 100"):
        repository.list_jobs(page_size=101)


def test_progress_serialization_failure_rolls_back_the_transition(training_context):
    """Catches a partial progress write when metrics cannot be persisted as JSON."""
    repository, _factory, admin_id = training_context
    repository.create_job(valid_job(created_by=admin_id))
    running = repository.claim_next()
    assert running is not None

    with pytest.raises(ValueError, match="JSON serializable"):
        repository.update_progress(
            running["id"], current_epoch=2, metrics={"loss": float("nan")}
        )

    stored = repository.get_job(running["id"])
    assert stored["current_epoch"] == 0
    assert stored["metrics"] is None


def test_terminal_metrics_are_decoded_and_artifacts_are_recorded(training_context):
    """Catches held-out metrics or artifact availability being lost at completion."""
    repository, session_factory, admin_id = training_context
    repository.create_job(valid_job(created_by=admin_id))
    running = repository.claim_next()
    assert running is not None
    metrics = {
        "precision": 0.87,
        "recall": 0.91,
        "map50": 0.9,
        "map50_95": 0.66,
        "no_helmet_precision": 0.86,
        "no_helmet_recall": 0.92,
        "accepted": True,
        "per_class": {"no_helmet": {"recall": 0.92}},
    }

    result = repository.mark_terminal(
        running["id"],
        status="completed",
        metrics=metrics,
        has_best=True,
        has_last=True,
        best_sha256="a" * 64,
    )

    assert result["metrics"] == metrics
    assert (result["has_best"], result["has_last"], result["has_metrics"]) == (
        True,
        True,
        True,
    )
    assert result["best_sha256"] == "a" * 64
    with session_factory() as session:
        row = session.get(TrainingJob, result["id"])
        assert row is not None
        assert row.metrics_json.startswith("{")


def test_terminal_job_cannot_be_rewritten_by_a_late_worker_result(training_context):
    """Catches a late process event changing immutable completed history."""
    repository, _factory, admin_id = training_context
    repository.create_job(valid_job(created_by=admin_id))
    running = repository.claim_next()
    assert running is not None
    completed = repository.mark_terminal(
        running["id"], status="completed", metrics={"map50": 0.9}, has_best=True
    )

    late = repository.mark_terminal(
        running["id"], status="failed", error_code="late_child_exit"
    )

    assert late == completed
