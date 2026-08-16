from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.batch.repository import BatchImageStateError, BatchRepository
from backend.batch.types import CompletedImage, PendingImage
from backend.db.migrate import run_migrations
from backend.db.models import BatchImage, User
from backend.db.session import create_engine_from_url, create_session_factory


NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def batch_context(tmp_path):
    database_url = "sqlite:///" + (tmp_path / "batch.db").as_posix()
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
        other = User(
            username="other",
            password_hash="unused",
            role="user",
            enabled=True,
            must_change_password=False,
        )
        session.add_all([owner, other])
        session.flush()
        owner_id, other_id = owner.id, other.id
    repository = BatchRepository(session_factory, clock=lambda: NOW)
    try:
        yield repository, session_factory, owner_id, other_id
    finally:
        engine.dispose()


def pending(name: str) -> PendingImage:
    return PendingImage(display_name=name, source_key="batch-source/" + name)


def completed(*, version: int | None = None) -> CompletedImage:
    return CompletedImage(
        annotated_key="batch-result/result.jpg",
        model_version_id=version,
        detections=[
            {"label": "helmet", "confidence": 0.91, "bbox": [1, 2, 30, 40]}
        ],
    )


def test_create_job_persists_owner_counts_files_and_thirty_day_expiry(batch_context):
    """Catches a job insert that loses ownership, file metadata, or retention time."""
    repository, _factory, owner_id, _other_id = batch_context

    job = repository.create_job(owner_id, [pending("甲.jpg"), pending("b.png")])

    assert (
        job.user_id,
        job.status,
        job.image_total,
        job.success_count,
        job.failure_count,
        job.expires_at,
    ) == (owner_id, "queued", 2, 0, 0, NOW + timedelta(days=30))
    assert [item.display_name for item in repository.list_images(job.id)] == [
        "甲.jpg",
        "b.png",
    ]
    assert [item.source_key for item in repository.list_images(job.id)] == [
        "batch-source/甲.jpg",
        "batch-source/b.png",
    ]


def test_create_job_rejects_empty_images_without_leaving_a_job(batch_context):
    """Catches accepting an unusable zero-image job or partially committing it."""
    repository, _factory, owner_id, _other_id = batch_context

    with pytest.raises(ValueError, match="at least one"):
        repository.create_job(owner_id, [])

    assert repository.list_jobs(user_id=owner_id).total == 0


def test_claim_uses_oldest_queued_image_and_marks_parent_processing(batch_context):
    """Catches non-deterministic claiming or claiming without updating job state."""
    repository, _factory, owner_id, _other_id = batch_context
    first_job = repository.create_job(owner_id, [pending("a.jpg"), pending("b.jpg")])
    repository.create_job(owner_id, [pending("c.jpg")])

    claimed = repository.claim_next_image()

    assert claimed is not None
    assert (claimed.job_id, claimed.display_name, claimed.source_key) == (
        first_job.id,
        "a.jpg",
        "batch-source/a.jpg",
    )
    assert repository.get_job(first_job.id).status == "processing"
    assert [item.status for item in repository.list_images(first_job.id)] == [
        "processing",
        "queued",
    ]


def test_claim_returns_none_when_no_queued_image_exists(batch_context):
    """Catches a worker busy-loop caused by returning a phantom claim."""
    repository, _factory, _owner_id, _other_id = batch_context

    assert repository.claim_next_image() is None


def test_partial_failure_recomputes_job_in_each_image_transaction(batch_context):
    """Catches stale counters or the wrong terminal state after mixed outcomes."""
    repository, _factory, owner_id, _other_id = batch_context
    job = repository.create_job(owner_id, [pending("a.jpg"), pending("b.jpg")])
    first = repository.claim_next_image()
    assert first is not None
    repository.complete_image(first.id, completed())
    second = repository.claim_next_image()
    assert second is not None

    repository.fail_image(second.id, "decode_failed", "图片无法解码")

    result = repository.get_job(job.id)
    assert result is not None
    assert (result.status, result.success_count, result.failure_count) == (
        "partial_failed",
        1,
        1,
    )
    images = repository.list_images(job.id)
    assert (images[1].status, images[1].error_code, images[1].error_message) == (
        "failed",
        "decode_failed",
        "图片无法解码",
    )


def test_completion_records_result_json_and_actual_model_version(batch_context):
    """Catches result data or model traceability being dropped at completion."""
    repository, session_factory, owner_id, _other_id = batch_context
    job = repository.create_job(owner_id, [pending("a.jpg")])
    claimed = repository.claim_next_image()
    assert claimed is not None

    repository.complete_image(claimed.id, completed(version=None))

    image = repository.list_images(job.id)[0]
    assert image.status == "completed"
    assert image.annotated_key == "batch-result/result.jpg"
    assert image.model_version_id is None
    assert image.detections == [
        {"label": "helmet", "confidence": 0.91, "bbox": [1, 2, 30, 40]}
    ]
    with session_factory() as session:
        stored = session.scalar(select(BatchImage).where(BatchImage.id == image.id))
        assert stored is not None
        assert stored.detections_json.startswith("[")


def test_terminal_write_rejects_an_image_that_was_not_claimed(batch_context):
    """Catches duplicate workers overwriting a queued or terminal image result."""
    repository, _factory, owner_id, _other_id = batch_context
    job = repository.create_job(owner_id, [pending("a.jpg")])
    image = repository.list_images(job.id)[0]

    with pytest.raises(BatchImageStateError, match="processing"):
        repository.complete_image(image.id, completed())

    assert repository.get_job(job.id).status == "queued"
    assert repository.list_images(job.id)[0].status == "queued"


def test_recover_interrupted_is_idempotent_and_preserves_terminal_images(batch_context):
    """Catches restart recovery losing work or repeatedly reporting the same rows."""
    repository, _factory, owner_id, _other_id = batch_context
    job = repository.create_job(owner_id, [pending("a.jpg"), pending("b.jpg")])
    first = repository.claim_next_image()
    assert first is not None
    repository.complete_image(first.id, completed())
    second = repository.claim_next_image()
    assert second is not None

    assert repository.recover_interrupted() == 1
    assert repository.recover_interrupted() == 0
    assert repository.get_job(job.id).status == "queued"
    assert [item.status for item in repository.list_images(job.id)] == [
        "completed",
        "queued",
    ]


def test_owner_filter_and_pagination_do_not_leak_other_users_jobs(batch_context):
    """Catches ordinary-user history queries exposing another user's jobs."""
    repository, _factory, owner_id, other_id = batch_context
    repository.create_job(owner_id, [pending("owner-1.jpg")])
    newest = repository.create_job(owner_id, [pending("owner-2.jpg")])
    repository.create_job(other_id, [pending("private.jpg")])

    first_page = repository.list_jobs(user_id=owner_id, page=1, page_size=1)

    assert first_page.total == 2
    assert [job.id for job in first_page.items] == [newest.id]
    assert repository.get_job(newest.id, user_id=other_id) is None
    assert repository.list_images(newest.id, user_id=other_id) == []


def test_list_expired_returns_file_keys_and_only_expired_jobs(batch_context):
    """Catches retention deleting a live job or lacking the keys needed for cleanup."""
    repository, _factory, owner_id, _other_id = batch_context
    job = repository.create_job(owner_id, [pending("a.jpg")])

    assert repository.list_expired(NOW + timedelta(days=29)) == []
    expired = repository.list_expired(NOW + timedelta(days=30))
    assert len(expired) == 1
    assert expired[0].id == job.id
    assert [(image.source_key, image.annotated_key) for image in expired[0].images] == [
        ("batch-source/a.jpg", None)
    ]


def test_claim_never_consumes_image_at_or_after_job_expiry(batch_context):
    repository, _factory, owner_id, _other_id = batch_context
    job = repository.create_job(owner_id, [pending("expired.jpg")])
    repository._clock = lambda: NOW + timedelta(days=30)

    assert repository.claim_next_image() is None
    assert repository.get_job(job.id).status == "queued"
    assert repository.list_images(job.id)[0].status == "queued"
