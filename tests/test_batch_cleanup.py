from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest

from backend.batch import BatchRepository, CompletedImage, PendingImage
from backend.batch.cleanup import RetentionCleaner
from backend.db.migrate import run_migrations
from backend.db.models import User
from backend.db.session import create_engine_from_url, create_session_factory
from backend.storage import LocalFileStorage


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class FailingDeleteStorage(LocalFileStorage):
    def __init__(self, root):
        super().__init__(root)
        self.failed_key: str | None = None

    def delete(self, key: str) -> None:
        if key == self.failed_key:
            raise OSError("simulated failure with a sensitive absolute path")
        super().delete(key)


@pytest.fixture
def cleanup_context(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "batch-cleanup.db").as_posix()
    run_migrations(database_url)
    engine = create_engine_from_url(database_url)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        owner = User(
            username="owner",
            password_hash="unused",
            role="user",
            enabled=True,
            must_change_password=False,
        )
        session.add(owner)
        session.flush()
        owner_id = owner.id
    repository = BatchRepository(session_factory, clock=lambda: NOW)
    storage = FailingDeleteStorage(tmp_path / "uploads")
    cleaner = RetentionCleaner(repository, storage)
    try:
        yield repository, storage, cleaner, owner_id
    finally:
        cleaner.stop()
        engine.dispose()


def _expired_completed_job(repository, storage, owner_id):
    source_key = storage.save(io.BytesIO(b"source"), "batch-source", "jpg")
    result_key = storage.save(io.BytesIO(b"result"), "batch-result", "jpg")
    job = repository.create_job(owner_id, [PendingImage("a.jpg", source_key)])
    claimed = repository.claim_next_image()
    assert claimed is not None
    repository.complete_image(
        claimed.id,
        CompletedImage(result_key, None, []),
    )
    return job, source_key, result_key


def test_cleanup_deletes_files_then_expired_database_record(cleanup_context):
    repository, storage, cleaner, owner_id = cleanup_context
    job, source_key, result_key = _expired_completed_job(
        repository, storage, owner_id
    )

    summary = cleaner.run_once(now=NOW + timedelta(days=30))

    assert (summary.scanned_jobs, summary.deleted_jobs, summary.failed_jobs) == (1, 1, 0)
    assert repository.get_job(job.id) is None
    for key in (source_key, result_key):
        with pytest.raises(FileNotFoundError):
            storage.open(key)


def test_cleanup_keeps_record_when_any_file_delete_fails(cleanup_context):
    repository, storage, cleaner, owner_id = cleanup_context
    job, source_key, result_key = _expired_completed_job(
        repository, storage, owner_id
    )
    storage.failed_key = source_key

    summary = cleaner.run_once(now=NOW + timedelta(days=30))

    assert (summary.deleted_jobs, summary.failed_jobs) == (0, 1)
    assert repository.get_job(job.id) is not None
    with storage.open(source_key) as stream:
        assert stream.read() == b"source"
    with pytest.raises(FileNotFoundError):
        storage.open(result_key)


def test_cleanup_missing_file_counts_as_already_deleted(cleanup_context):
    repository, storage, cleaner, owner_id = cleanup_context
    job, source_key, _result_key = _expired_completed_job(
        repository, storage, owner_id
    )
    storage.delete(source_key)

    summary = cleaner.run_once(now=NOW + timedelta(days=30))

    assert summary.deleted_jobs == 1
    assert repository.get_job(job.id) is None


def test_cleanup_rejects_naive_time_without_touching_records(cleanup_context):
    repository, storage, cleaner, owner_id = cleanup_context
    job, _source_key, _result_key = _expired_completed_job(
        repository, storage, owner_id
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        cleaner.run_once(now=datetime(2030, 2, 1))

    assert repository.get_job(job.id) is not None


def test_cleanup_retains_database_record_when_key_attempts_path_escape(cleanup_context):
    repository, _storage, cleaner, owner_id = cleanup_context
    job = repository.create_job(
        owner_id,
        [PendingImage("unsafe.jpg", "../outside.jpg")],
    )

    summary = cleaner.run_once(now=NOW + timedelta(days=30))

    assert (summary.deleted_jobs, summary.failed_jobs) == (0, 1)
    assert repository.get_job(job.id) is not None


def test_cleanup_skips_expired_job_while_an_image_is_processing(cleanup_context):
    repository, storage, cleaner, owner_id = cleanup_context
    source_key = storage.save(io.BytesIO(b"source"), "batch-source", "jpg")
    job = repository.create_job(owner_id, [PendingImage("active.jpg", source_key)])
    assert repository.claim_next_image() is not None

    summary = cleaner.run_once(now=NOW + timedelta(days=30))

    assert summary.skipped_jobs == 1
    assert summary.deleted_jobs == 0
    assert repository.get_job(job.id) is not None
    assert repository.list_images(job.id)[0].status == "processing"
    with storage.open(source_key) as stream:
        assert stream.read() == b"source"


def test_cleanup_rereads_current_keys_after_stale_candidate_snapshot(
    cleanup_context,
    monkeypatch,
):
    repository, storage, cleaner, owner_id = cleanup_context
    source_key = storage.save(io.BytesIO(b"source"), "batch-source", "jpg")
    result_key = storage.save(io.BytesIO(b"result"), "batch-result", "jpg")
    job = repository.create_job(owner_id, [PendingImage("race.jpg", source_key)])
    claimed = repository.claim_next_image()
    assert claimed is not None
    original_list_expired = repository.list_expired

    def stale_then_complete(now):
        stale = original_list_expired(now)
        repository.complete_image(
            claimed.id,
            CompletedImage(result_key, None, []),
        )
        return stale

    monkeypatch.setattr(repository, "list_expired", stale_then_complete)

    summary = cleaner.run_once(now=NOW + timedelta(days=30))

    assert summary.deleted_jobs == 1
    assert repository.get_job(job.id) is None
    for key in (source_key, result_key):
        with pytest.raises(FileNotFoundError):
            storage.open(key)
