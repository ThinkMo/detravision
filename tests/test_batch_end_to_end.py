from __future__ import annotations

import os
import time
import traceback
import warnings
import zipfile
from datetime import timedelta
from io import BytesIO
from multiprocessing import get_context
from pathlib import Path

import pytest

from backend.batch import PendingImage
from backend.core.config import Settings
from backend.main import create_app


def _checkout_roots() -> list[Path]:
    checkout = Path(__file__).resolve().parents[1]
    roots = [checkout]
    if checkout.parent.name == ".worktrees":
        roots.append(checkout.parent.parent)
    return roots


def _real_model_path() -> Path:
    configured = os.getenv("REAL_MODEL_PATH")
    candidates = ([Path(configured)] if configured else []) + [
        root / "runs" / "detect" / "train-3" / "weights" / "best.pt"
        for root in _checkout_roots()
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    pytest.skip("set REAL_MODEL_PATH to an existing trained best.pt")


def _helmet_test_images() -> list[Path]:
    configured = os.getenv("REAL_MODEL_TEST_IMAGE_DIR")
    directories = ([Path(configured)] if configured else []) + [
        root / "datasets" / "construction-ppe" / "images" / "test"
        for root in _checkout_roots()
    ]
    for directory in directories:
        images = sorted(
            path
            for path in directory.glob("*")
            if path.suffix.casefold() in {".jpg", ".jpeg", ".png", ".webp"}
        )
        if len(images) >= 2:
            return images[:2]
    pytest.skip("set REAL_MODEL_TEST_IMAGE_DIR to a directory with two test images")


def test_batch_settings_drive_retention_and_reject_parallel_workers(tmp_path):
    """Catches the app ignoring configured retention or enabling unsafe parallel workers."""
    from fastapi.testclient import TestClient

    with pytest.raises(ValueError, match="BATCH_WORKER_COUNT"):
        Settings(batch_worker_count=2)

    database_url = "sqlite:///" + (tmp_path / "retention.db").as_posix()
    app = create_app(
        Settings(
            database_url=database_url,
            upload_dir=tmp_path / "uploads",
            snapshot_dir=tmp_path / "snapshots",
            enable_runtime=False,
            batch_retention_days=7,
            batch_worker_count=1,
        )
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123456"},
        )
        assert login.status_code == 200
        created = app.state.batch_repository.create_job(
            login.json()["id"],
            [PendingImage(display_name="retention.jpg", source_key="missing")],
        )

    retention = created.expires_at - created.created_at
    assert retention == timedelta(days=7)


def _run_real_model_smoke(
    model_path_text: str,
    image_path_texts: list[str],
    temp_root_text: str,
    result_queue,
) -> None:
    """Run Ultralytics in a child process so its global PIL patches cannot leak."""
    try:
        from starlette.exceptions import StarletteDeprecationWarning

        warnings.filterwarnings(
            "ignore",
            message="Using `httpx` with `starlette.testclient` is deprecated.*",
            category=StarletteDeprecationWarning,
        )
        from fastapi.testclient import TestClient

        model_path = Path(model_path_text)
        images = [Path(value) for value in image_path_texts]
        temp_root = Path(temp_root_text)
        upload_dir = temp_root / "uploads"
        yolo_config_dir = temp_root / "yolo-config"
        matplotlib_config_dir = temp_root / "matplotlib-config"
        yolo_config_dir.mkdir()
        matplotlib_config_dir.mkdir()
        os.environ["YOLO_CONFIG_DIR"] = str(yolo_config_dir)
        os.environ["MPLCONFIGDIR"] = str(matplotlib_config_dir)
        database_url = "sqlite:///" + (temp_root / "real-model.db").as_posix()
        app = create_app(
            Settings(
                database_url=database_url,
                upload_dir=upload_dir,
                snapshot_dir=temp_root / "snapshots",
                model_path=str(model_path),
                enable_runtime=False,
                batch_retention_days=30,
                batch_worker_count=1,
            )
        )

        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "admin123456"},
            )
            assert login.status_code == 200
            changed = client.post(
                "/api/auth/change-password",
                json={
                    "current_password": "admin123456",
                    "new_password": "Real-model-smoke-123",
                },
            )
            assert changed.status_code == 204
            relogin = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "Real-model-smoke-123"},
            )
            assert relogin.status_code == 200

            files = [
                ("files", (path.name, path.read_bytes(), "image/jpeg"))
                for path in images
            ]
            response = client.post("/api/batch-jobs", files=files)
            assert response.status_code == 201
            job_id = response.json()["id"]

            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                detail = client.get(f"/api/batch-jobs/{job_id}")
                assert detail.status_code == 200
                job = detail.json()
                if job["status"] not in {"queued", "processing"}:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("real-model batch did not finish within 60 seconds")

            assert job["status"] in {"completed", "partial_failed"}
            completed = [
                image for image in job["images"] if image["status"] == "completed"
            ]
            assert len(completed) == len(images)
            assert all(image["result_url"] for image in completed)
            assert all(image["preview_url"] for image in completed)

            detections = [
                detection
                for image in completed
                for detection in image["detections"]
            ]
            assert detections
            assert all(
                {"label", "confidence", "bbox"}.issubset(detection)
                for detection in detections
            )
            assert any(
                detection["label"] in {"person", "helmet"}
                for detection in detections
            )
            no_helmet_count = sum(
                detection["label"] == "no_helmet" for detection in detections
            )
            assert 0 <= no_helmet_count <= len(detections)

            for image in completed:
                preview = client.get(image["preview_url"])
                assert preview.status_code == 200
                assert preview.headers["content-type"].startswith("image/jpeg")
                assert preview.headers["content-disposition"].startswith("inline;")
                assert preview.headers["x-content-type-options"] == "nosniff"
                assert preview.content.startswith(b"\xff\xd8")

            for image in completed:
                image_download = client.get(image["result_url"])
                assert image_download.status_code == 200
                assert image_download.headers["content-type"].startswith("image/jpeg")
                assert image_download.headers["content-disposition"].startswith(
                    "attachment;"
                )
                assert image_download.content.startswith(b"\xff\xd8")

            archive_response = client.get(f"/api/batch-jobs/{job_id}/download")
            assert archive_response.status_code == 200
            with zipfile.ZipFile(BytesIO(archive_response.content)) as archive:
                names = archive.namelist()
                assert "results.json" in names
                assert any(name.endswith(".jpg") for name in names)

        assert upload_dir.is_dir()
        result_queue.put(
            {
                "model": model_path.name,
                "images": [path.name for path in images],
                "job_status": job["status"],
                "image_statuses": [image["status"] for image in job["images"]],
                "detections": [len(image["detections"]) for image in job["images"]],
                "detection_labels": [
                    [detection["label"] for detection in image["detections"]]
                    for image in job["images"]
                ],
                "no_helmet_count": no_helmet_count,
            }
        )
    except BaseException:
        result_queue.put({"error": traceback.format_exc()})
        raise


@pytest.mark.model
def test_real_model_batch_smoke_uses_isolated_sqlite_and_downloads(tmp_path):
    """Catches the trained detector, worker, persistence, and downloads failing together."""
    model_path = _real_model_path()
    images = _helmet_test_images()
    context = get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_run_real_model_smoke,
        args=(
            str(model_path),
            [str(path) for path in images],
            str(tmp_path),
            result_queue,
        ),
    )
    process.start()
    process.join(timeout=90)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        pytest.fail("real-model smoke child process exceeded 90 seconds")

    result = result_queue.get(timeout=5)
    result_queue.close()
    result_queue.join_thread()
    assert process.exitcode == 0, result.get("error", "real-model child failed")
    assert "error" not in result, result["error"]
    assert result["job_status"] in {"completed", "partial_failed"}
    assert "completed" in result["image_statuses"]
    print(
        "REAL_MODEL_RESULT "
        f"model={result['model']} "
        f"images={','.join(result['images'])} "
        f"job_status={result['job_status']} "
        f"image_statuses={','.join(result['image_statuses'])} "
        f"detections={','.join(str(value) for value in result['detections'])} "
        f"detection_labels={'|'.join(','.join(labels) for labels in result['detection_labels'])} "
        f"no_helmet_count={result['no_helmet_count']}"
    )
