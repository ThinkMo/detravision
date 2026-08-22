from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.config import Settings
from backend.training.storage import ARTIFACTS, TrainingStorage


def make_storage(tmp_path: Path, *, tail_bytes: int = 32) -> TrainingStorage:
    settings = Settings(
        training_dataset_yaml=tmp_path / "secret-data" / "dataset.yaml",
        training_run_dir=tmp_path / "secret-runs",
        training_model_cache_dir=tmp_path / "secret-models",
        training_log_tail_bytes=tail_bytes,
    )
    return TrainingStorage(settings)


def test_create_run_uses_distinct_opaque_keys_and_contained_directories(tmp_path: Path):
    """Catches predictable or escaped training directories being allocated."""
    storage = make_storage(tmp_path)

    first = storage.create_run()
    second = storage.create_run()

    assert first != second
    assert len(first) == len("run-") + 32
    assert first.startswith("run-")
    assert all(character in "0123456789abcdef" for character in first[4:])
    assert storage.run_path(first).is_dir()
    assert storage.run_path(first).is_relative_to(storage.root)


def test_artifact_resolution_uses_only_the_fixed_allowlist(tmp_path: Path):
    """Catches API callers selecting arbitrary filenames below a run."""
    storage = make_storage(tmp_path)
    run_key = storage.create_run()

    assert ARTIFACTS == {
        "log": "training.log",
        "progress": "progress.json",
        "result": "result.json",
        "best": "weights/best.pt",
        "last": "weights/last.pt",
        "confusion_matrix": "confusion_matrix.png",
    }
    assert storage.resolve_artifact(run_key, "best") == (
        storage.root / run_key / "weights" / "best.pt"
    ).resolve()
    with pytest.raises(ValueError):
        storage.resolve_artifact(run_key, "../../outside")


@pytest.mark.parametrize(
    "run_key",
    [
        "../run-0123456789abcdef0123456789abcdef",
        "C:\\private\\run-0123456789abcdef0123456789abcdef",
        "C:run-0123456789abcdef0123456789abcdef",
        "\\\\server\\share\\run-0123456789abcdef0123456789abcdef",
        "/var/private/run-0123456789abcdef0123456789abcdef",
        "run-0123456789abcdef0123456789abcdef/../other",
    ],
)
def test_run_path_rejects_drive_unc_posix_and_traversal_inputs(
    tmp_path: Path,
    run_key: str,
):
    """Catches cross-platform absolute and traversal paths escaping storage."""
    storage = make_storage(tmp_path)

    with pytest.raises(ValueError):
        storage.run_path(run_key)


def test_artifact_resolution_rejects_a_run_symlink_that_escapes_root(tmp_path: Path):
    """Catches a contained-looking run symlink redirecting artifact access outside."""
    storage = make_storage(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = storage.root / "run-0123456789abcdef0123456789abcdef"
    storage.root.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(ValueError):
        storage.resolve_artifact(link.name, "log")


def test_tail_log_is_byte_bounded_and_decodes_partial_utf8_safely(tmp_path: Path):
    """Catches log responses exceeding their configured byte budget or decode failure."""
    storage = make_storage(tmp_path, tail_bytes=9)
    run_key = storage.create_run()
    log = storage.resolve_artifact(run_key, "log")
    log.write_bytes(b"prefix-" + "安全帽".encode("utf-8") + b"-tail")

    result = storage.tail_log(run_key, limit=1000)

    assert len(result.encode("utf-8")) <= 15  # replacement chars may expand partial UTF-8
    assert result.endswith("-tail")
    assert "prefix" not in result


def test_tail_log_respects_a_smaller_caller_limit(tmp_path: Path):
    """Catches a requested smaller tail limit being ignored."""
    storage = make_storage(tmp_path, tail_bytes=64)
    run_key = storage.create_run()
    storage.resolve_artifact(run_key, "log").write_bytes(b"0123456789")

    assert storage.tail_log(run_key, limit=4) == "6789"
    with pytest.raises(ValueError):
        storage.tail_log(run_key, limit=0)


def test_scrub_log_removes_configured_windows_and_posix_root_spellings(tmp_path: Path):
    """Catches server-local configured roots leaking in returned logs."""
    storage = make_storage(tmp_path)
    windows_root = str(storage.root)
    posix_root = storage.root.as_posix()
    model_root = str(storage.model_cache_root)
    dataset_path = str(storage.dataset_yaml)
    text = f"run={windows_root} posix={posix_root} model={model_root} data={dataset_path}"

    scrubbed = storage.scrub_log(text)

    assert windows_root not in scrubbed
    assert posix_root not in scrubbed
    assert model_root not in scrubbed
    assert dataset_path not in scrubbed
    assert scrubbed.count("[redacted]") >= 3


def test_missing_log_has_an_empty_safe_tail(tmp_path: Path):
    """Catches a normal not-yet-started job surfacing a filesystem error."""
    storage = make_storage(tmp_path)
    run_key = storage.create_run()

    assert storage.tail_log(run_key, limit=32) == ""
