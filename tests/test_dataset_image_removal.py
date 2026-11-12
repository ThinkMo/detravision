from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from test_annotation_service import annotation_context
from test_training_api import _app
from backend.training.annotation_service import AnnotationServiceError
from backend.training.dataset_service import DatasetService
from backend.core.config import Settings


def selection(image):
    return [{"id": image["id"], "revision": image["revision"]}]


def test_remove_archives_image_and_label_without_touching_source_and_rescan_stays_removed(annotation_context):
    service, repo, path, dataset, image = annotation_context
    source = path.parent / "source/images/test/image.jpg"
    original = source.read_bytes()
    image = service.confirm_empty(dataset["id"], image["id"], 0)
    result = service.remove_images(dataset["id"], dataset["revision"], selection(image))
    assert result == {"removed": 1, "image_ids": [image["id"]], "dataset_revision": 1}
    assert repo.get_image(dataset["id"], image["id"]) is None
    assert not (path / "images/test/image.jpg").exists()
    assert not (path / "labels/test/image.txt").exists()
    assert source.read_bytes() == original
    assert next((path / ".removed-images").rglob("image.jpg")).read_bytes() == original
    assert next((path / ".removed-images").rglob("image.txt")).read_bytes() == b""
    settings = Settings(training_dataset_roots_raw=f"primary={path.parent}")
    DatasetService(settings, repo).rescan(dataset["id"])
    assert repo.list_images(dataset["id"], "test")["total"] == 0


def test_batch_remove_is_all_or_nothing_when_one_member_is_stale(annotation_context):
    service, repo, path, dataset, image = annotation_context
    Image.new("RGB", (80, 60), "blue").save(path / "images/test/second.jpg")
    datasets = DatasetService(Settings(training_dataset_roots_raw=f"primary={path.parent}"), repo)
    datasets.rescan(dataset["id"])
    second = next(r for r in repo.list_images(dataset["id"], "test")["items"] if r["display_name"] == "second.jpg")
    before_revision = repo.get(dataset["id"])["revision"]
    with pytest.raises(AnnotationServiceError) as error:
        service.remove_images(dataset["id"], before_revision, [*selection(image), {"id": second["id"], "revision": 5}])
    assert error.value.code == "annotation_conflict"
    assert repo.list_images(dataset["id"], "test")["total"] == 2
    assert (path / "images/test/image.jpg").exists()
    assert (path / "images/test/second.jpg").exists()
    assert repo.get(dataset["id"])["revision"] == before_revision
    result = service.remove_images(dataset["id"], before_revision, [*selection(image), *selection(second)])
    assert result["removed"] == 2
    assert repo.list_images(dataset["id"], "test")["total"] == 0


@pytest.mark.parametrize("kind", ["dataset_revision", "foreign_image", "published", "duplicate", "empty"])
def test_remove_rejects_invalid_or_readonly_selection_without_moving_files(annotation_context, kind):
    service, repo, path, dataset, image = annotation_context
    did, revision, selected = dataset["id"], 0, selection(image)
    expected = "annotation_conflict"
    if kind == "dataset_revision": revision = 9
    if kind == "foreign_image":
        selected = [{"id": image["id"] + 10000, "revision": 0}]
        expected = "annotation_not_found"
    if kind == "published":
        repo.publish(did, 0, "a" * 64)
        expected = "dataset_published"
    if kind == "duplicate":
        selected *= 2
        expected = "annotation_invalid"
    if kind == "empty":
        selected = []
        expected = "annotation_invalid"
    with pytest.raises(AnnotationServiceError) as error:
        service.remove_images(did, revision, selected)
    assert error.value.code == expected
    assert (path / "images/test/image.jpg").exists()
    assert repo.get_image(did, image["id"]) is not None


def test_remove_restores_files_and_index_if_archiving_a_label_fails(annotation_context, monkeypatch):
    import os
    service, repo, path, dataset, image = annotation_context
    image = service.confirm_empty(dataset["id"], image["id"], 0)
    real_replace = os.replace
    def fail_label(source, target):
        if Path(source) == path / "labels/test/image.txt":
            raise OSError("disk unavailable")
        return real_replace(source, target)
    monkeypatch.setattr(os, "replace", fail_label)
    with pytest.raises(AnnotationServiceError) as error:
        service.remove_images(dataset["id"], 0, selection(image))
    assert error.value.code == "annotation_write_failed"
    assert (path / "images/test/image.jpg").exists()
    assert (path / "labels/test/image.txt").exists()
    assert repo.get_image(dataset["id"], image["id"])["review_status"] == "confirmed_empty"
    assert repo.get(dataset["id"])["revision"] == 0


def test_remove_rejects_a_resolved_path_outside_its_dataset(annotation_context, monkeypatch):
    service, repo, path, dataset, image = annotation_context
    outside = path.parent / "source/images/test/image.jpg"
    monkeypatch.setattr(service._filesystem, "resolve_image", lambda *_: outside)
    with pytest.raises(AnnotationServiceError) as error:
        service.remove_images(dataset["id"], 0, selection(image))
    assert error.value.code == "annotation_invalid"
    assert outside.exists() and (path / "images/test/image.jpg").exists()


def test_remove_endpoint_uses_real_service_and_requires_admin_and_origin(annotation_context, tmp_path):
    service, repo, path, dataset, image = annotation_context
    payload = {"revision": 0, "images": selection(image)}
    url = f"/api/training/datasets/{dataset['id']}/images/remove"
    (tmp_path / "app").mkdir()
    app, _, audit = _app(tmp_path / "app")
    app.state.annotation_service = service
    with TestClient(app) as client:
        assert client.post(url, json=payload, headers={"Origin": "http://other-site"}).status_code == 403
        assert client.post(url, json={"revision": 0, "images": []}, headers={"Origin": "http://testserver"}).status_code == 422
        response = client.post(url, json=payload, headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert response.json()["removed"] == 1
    assert str(path) not in response.text
    assert repo.get_image(dataset["id"], image["id"]) is None
    assert any("dataset_image_remove" in str(row) for row in audit.rows)
    (tmp_path / "regular").mkdir()
    regular, _, _ = _app(tmp_path / "regular", role="user")
    regular.state.annotation_service = service
    with TestClient(regular) as client:
        assert client.post(url, json=payload, headers={"Origin": "http://testserver"}).status_code == 403
def test_remove_restores_archived_files_on_database_failure_after_files_move(annotation_context, monkeypatch):
    from contextlib import contextmanager
    from sqlalchemy.exc import SQLAlchemyError
    service, repo, path, dataset, image = annotation_context
    real_transaction = repo.removing_images
    @contextmanager
    def failing_transaction(*args):
        with real_transaction(*args) as revision:
            yield revision
            raise SQLAlchemyError("commit failed")
    monkeypatch.setattr(repo, "removing_images", failing_transaction)
    with pytest.raises(AnnotationServiceError) as error:
        service.remove_images(dataset["id"], 0, selection(image))
    assert error.value.code == "annotation_write_failed"
    assert (path / "images/test/image.jpg").exists()
    assert repo.get_image(dataset["id"], image["id"]) is not None
    assert repo.get(dataset["id"])["revision"] == 0


def test_remove_refuses_an_image_whose_bytes_changed_since_it_was_loaded(annotation_context):
    service, repo, path, dataset, image = annotation_context
    target = path / "images/test/image.jpg"
    Image.new("RGB", (100, 80), "red").save(target)
    changed = target.read_bytes()
    with pytest.raises(AnnotationServiceError) as error:
        service.remove_images(dataset["id"], 0, selection(image))
    assert error.value.code == "annotation_conflict"
    assert target.read_bytes() == changed
    assert repo.get_image(dataset["id"], image["id"]) is not None
@pytest.mark.parametrize("kind", ["image", "label"])
def test_remove_does_not_follow_a_resolved_alias_to_an_unselected_file(annotation_context, monkeypatch, kind):
    service, repo, path, dataset, image = annotation_context
    if kind == "label":
        image = service.confirm_empty(dataset["id"], image["id"], 0)
    original = path / ("images/test/image.jpg" if kind == "image" else "labels/test/image.txt")
    other = original.with_name("unselected" + original.suffix)
    other.write_bytes(original.read_bytes())
    monkeypatch.setattr(service._filesystem, "resolve_image" if kind == "image" else "resolve_label", lambda *_: other)
    with pytest.raises(AnnotationServiceError) as error:
        service.remove_images(dataset["id"], 0, selection(image))
    assert error.value.code == "annotation_invalid"
    assert original.exists() and other.exists()
    assert repo.get_image(dataset["id"], image["id"]) is not None
def test_scan_started_before_removal_cannot_reintroduce_the_removed_image(annotation_context, monkeypatch):
    from backend.training.dataset_service import DatasetServiceError
    service, repo, path, dataset, image = annotation_context
    datasets = DatasetService(Settings(training_dataset_roots_raw=f"primary={path.parent}"), repo)
    original_scan = datasets._filesystem.scan
    def scan_then_remove(*args):
        result = original_scan(*args)
        service.remove_images(dataset["id"], 0, selection(image))
        return result
    monkeypatch.setattr(datasets._filesystem, "scan", scan_then_remove)
    with pytest.raises(DatasetServiceError) as error:
        datasets.rescan(dataset["id"])
    assert error.value.code == "dataset_conflict"
    assert repo.list_images(dataset["id"], "test")["total"] == 0
    assert not (path / "images/test/image.jpg").exists()