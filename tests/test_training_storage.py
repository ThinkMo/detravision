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


@pytest.mark.parametrize("limit", [True, 4.0, float("nan"), float("inf")])
def test_tail_log_rejects_non_integer_limits(tmp_path: Path, limit: object):
    """Catches ambiguous or non-finite limits bypassing the byte bound."""
    storage = make_storage(tmp_path)
    run_key = storage.create_run()

    with pytest.raises(ValueError):
        storage.tail_log(run_key, limit=limit)


def test_tail_log_scrubs_arbitrary_absolute_paths_and_traceback_source(tmp_path: Path):
    """Catches non-configured server paths and source lines leaking through logs."""
    storage = make_storage(tmp_path, tail_bytes=4096)
    run_key = storage.create_run()
    log = storage.resolve_artifact(run_key, "log")
    log.write_text(
        "weights=D:\\private\\customer\\best.pt\n"
        "spaced=D:\\Private Data\\customer model\\best.pt\n"
        "share=\\\\server\\secret\\dataset\\images\n"
        "config=/srv/safety/private/config.yaml\n"
        "Traceback (most recent call last):\n"
        '  File "C:\\Python312\\Lib\\worker.py", line 9, in run\n'
        "    secret = open('/srv/credentials/token')\n"
        "RuntimeError: training failed\n",
        encoding="utf-8",
    )

    result = storage.tail_log(run_key, limit=4096)

    for secret in (
        "D:\\private",
        "Private Data",
        "customer model",
        "\\\\server\\secret",
        "/srv/safety",
        "C:\\Python312",
        "worker.py",
        "secret = open",
        "/srv/credentials",
    ):
        assert secret not in result
    assert "RuntimeError: training failed" in result


def test_tail_log_scrubs_network_paths_and_local_file_uris_only(tmp_path: Path):
    """Catches slash-UNC and file URIs leaking without destroying ordinary URLs."""
    storage = make_storage(tmp_path, tail_bytes=4096)
    run_key = storage.create_run()
    storage.resolve_artifact(run_key, "log").write_text(
        "artifact=//server/share/private/best.pt\n"
        "posix=FILE:///srv/private/token\n"
        "drive=file://C:/Private/customer/token.txt\n"
        "host=file://server/share/private/key.txt\n"
        "docs=https://example.test/training/help\n"
        "api=http://localhost:8000/health\n"
        "division=left//right\n",
        encoding="utf-8",
    )

    result = storage.tail_log(run_key, limit=4096)

    for secret in (
        "//server/share/private",
        "file:///srv/private",
        "file://C:/Private",
        "file://server/share/private",
        "token.txt",
        "key.txt",
    ):
        assert secret.casefold() not in result.casefold()
    assert "https://example.test/training/help" in result
    assert "http://localhost:8000/health" in result
    assert "left//right" in result


def test_tail_log_scrubs_slash_unc_paths_after_punctuation(tmp_path: Path):
    """Catches punctuated slash-UNC paths bypassing the line safety boundary."""
    storage = make_storage(tmp_path, tail_bytes=4096)
    run_key = storage.create_run()
    storage.resolve_artifact(run_key, "log").write_text(
        "bracket=[//server/share/private/best.pt]\n"
        "paths=[//server/share/a,//server/two/b]\n"
        "object={//server/share/private/key}\n"
        "comma=value,//server/share/private/item\n"
        "docs=https://example.test/training/help\n"
        "api=http://localhost:8000/health\n"
        "division=left//right\n",
        encoding="utf-8",
    )

    result = storage.tail_log(run_key, limit=4096)

    for secret in (
        "//server/share/private/best.pt",
        "//server/share/a",
        "//server/two/b",
        "//server/share/private/key",
        "//server/share/private/item",
    ):
        assert secret not in result
    assert "https://example.test/training/help" in result
    assert "http://localhost:8000/health" in result
    assert "left//right" in result


def test_tail_log_does_not_leak_a_path_when_read_starts_in_its_middle(tmp_path: Path):
    """Catches byte-tail seeking exposing a suffix of an absolute private path."""
    storage = make_storage(tmp_path, tail_bytes=128)
    run_key = storage.create_run()
    storage.resolve_artifact(run_key, "log").write_text(
        "old line\n"
        + "D:\\private\\customer\\"
        + ("nested\\" * 40)
        + "secret.pt\nSAFE-END",
        encoding="utf-8",
    )

    result = storage.tail_log(run_key, limit=32)

    assert "private" not in result.casefold()
    assert "nested" not in result.casefold()
    assert "secret.pt" not in result.casefold()
    assert result.endswith("SAFE-END")


def test_tail_log_return_is_strictly_bounded_after_utf8_replacement(tmp_path: Path):
    """Catches UTF-8 replacement characters expanding the returned byte length."""
    storage = make_storage(tmp_path, tail_bytes=64)
    run_key = storage.create_run()
    storage.resolve_artifact(run_key, "log").write_bytes("前缀安全帽".encode("utf-8"))

    result = storage.tail_log(run_key, limit=5)

    assert len(result.encode("utf-8")) <= 5


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
