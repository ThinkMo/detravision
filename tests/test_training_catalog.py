from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.config import Settings
from backend.training.catalog import (
    MAX_DATASET_YAML_BYTES,
    MODEL_IDS,
    TrainingRequest,
    training_catalog,
)


EXPECTED_MODEL_IDS = (
    "yolo11n.pt",
    "yolo11s.pt",
    "yolo11m.pt",
    "yolo11l.pt",
    "yolo11x.pt",
    "yolo26n.pt",
    "yolo26s.pt",
    "yolo26m.pt",
    "yolo26l.pt",
    "yolo26x.pt",
)


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    dataset = tmp_path / "private" / "helmet.yaml"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text(
        "train: images/train\nval: images/val\ntest: images/test\n"
        "names:\n  0: person\n  1: helmet\n  2: no_helmet\n",
        encoding="utf-8",
    )
    values: dict[str, object] = {
        "training_dataset_yaml": dataset,
        "training_dataset_roots_raw": f"primary={tmp_path}",
        "training_run_dir": tmp_path / "runs",
        "training_model_cache_dir": tmp_path / "models",
    }
    values.update(overrides)
    return Settings(**values)


def test_catalog_exposes_only_fixed_models_and_disabled_remote(tmp_path: Path):
    """Catches arbitrary weights or an accidentally enabled remote executor."""
    catalog = training_catalog(make_settings(tmp_path))

    assert MODEL_IDS == EXPECTED_MODEL_IDS
    assert tuple(model["id"] for model in catalog["models"]) == EXPECTED_MODEL_IDS
    assert catalog["executors"] == [
        {"id": "local", "enabled": True},
        {"id": "remote", "enabled": False},
    ]


def test_catalog_reports_local_weight_availability(tmp_path: Path):
    settings = make_settings(tmp_path)
    settings.training_model_cache_dir.mkdir()
    (settings.training_model_cache_dir / "yolo11s.pt").write_bytes(b"weights")

    catalog = training_catalog(settings)

    availability = {model["id"]: model["available"] for model in catalog["models"]}
    assert availability["yolo11s.pt"] is True
    assert availability["yolo11m.pt"] is False


def test_catalog_summarizes_dataset_without_exposing_server_path(tmp_path: Path):
    """Catches configured dataset paths leaking through the public catalog DTO."""
    settings = make_settings(tmp_path)

    catalog = training_catalog(settings)

    assert catalog["dataset"] == {
        "id": "configured",
        "available": True,
        "has_test_split": True,
        "classes": ["person", "helmet", "no_helmet"],
    }
    serialized = repr(catalog)
    assert str(settings.training_dataset_yaml) not in serialized
    assert str(tmp_path) not in serialized


@pytest.mark.parametrize(
    "yaml_text",
    [
        "train: images/train\nval: images/val\nnames: [person]\n",
        "train: ''\nval: images/val\ntest: images/test\nnames: [person]\n",
        "train: images/train\nval: []\ntest: images/test\nnames: [person]\n",
        "train: images/train\nval: images/val\ntest: images/test\nnames: []\n",
        "train: images/train\nval: images/val\ntest: images/test\nnames: wrong\n",
        "- train\n- val\n- test\n",
    ],
)
def test_catalog_marks_incomplete_or_wrongly_typed_dataset_unavailable(
    tmp_path: Path,
    yaml_text: str,
):
    """Catches malformed dataset summaries being advertised as trainable."""
    settings = make_settings(tmp_path)
    settings.training_dataset_yaml.write_text(yaml_text, encoding="utf-8")

    assert training_catalog(settings)["dataset"] == {
        "id": "configured",
        "available": False,
        "has_test_split": False,
        "classes": [],
    }


def test_catalog_rejects_dataset_yaml_larger_than_fixed_budget(tmp_path: Path):
    """Catches an oversized configured YAML consuming unbounded parser resources."""
    settings = make_settings(tmp_path)
    valid_prefix = (
        b"train: images/train\nval: images/val\ntest: images/test\nnames: [person]\n"
    )
    padding = b"#" * (MAX_DATASET_YAML_BYTES + 1 - len(valid_prefix))
    settings.training_dataset_yaml.write_bytes(valid_prefix + padding)

    assert training_catalog(settings)["dataset"]["available"] is False


def test_catalog_fails_closed_for_deeply_nested_yaml(tmp_path: Path):
    """Catches parser recursion/resource failures escaping the catalog boundary."""
    settings = make_settings(tmp_path)
    deeply_nested = "root:\n" + "".join(
        f"{'  ' * depth}level{depth}:\n" for depth in range(1, 600)
    )
    assert len(deeply_nested.encode("utf-8")) < MAX_DATASET_YAML_BYTES
    settings.training_dataset_yaml.write_text(deeply_nested, encoding="utf-8")

    assert training_catalog(settings)["dataset"]["available"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("executor", "remote"),
        ("executor", "ssh"),
        ("base_model", "../../evil.pt"),
        ("base_model", "yolo11z.pt"),
        ("imgsz", 639),
        ("imgsz", 1280),
        ("epochs", 0),
        ("epochs", 1001),
        ("batch", "16"),
        ("device", "0,1"),
        ("patience", -1),
        ("patience", 501),
    ],
)
def test_training_request_rejects_values_outside_safe_catalog_and_bounds(
    field: str,
    value: object,
):
    """Catches untrusted paths, executors, or unbounded values reaching a worker."""
    payload: dict[str, object] = {
        "dataset_key": "dataset:1",
        "executor": "local",
        "base_model": "yolo11m.pt",
        "imgsz": 640,
        "epochs": 100,
        "batch": "auto",
        "device": "auto",
        "patience": 50,
    }
    payload[field] = value

    with pytest.raises(ValueError):
        TrainingRequest(**payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("imgsz", 640.0),
        ("epochs", "100"),
        ("epochs", True),
        ("patience", "50"),
        ("patience", False),
    ],
)
def test_training_request_rejects_values_with_unsafe_types(
    field: str,
    value: object,
):
    """Catches coercible or incomparable values bypassing stable request validation."""
    payload: dict[str, object] = {
        "dataset_key": "dataset:1",
        "executor": "local",
        "base_model": "yolo11m.pt",
        "imgsz": 640,
        "epochs": 100,
        "batch": "auto",
        "device": "auto",
        "patience": 50,
    }
    payload[field] = value

    with pytest.raises(ValueError):
        TrainingRequest(**payload)


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("imgsz", (640, 960)),
        ("epochs", (1, 1000)),
        ("batch", ("1", "2", "4", "8", "auto")),
        ("device", ("auto", "cpu", "0")),
        ("patience", (0, 500)),
    ],
)
def test_training_request_accepts_documented_boundary_values(
    field: str,
    values: tuple,
):
    """Catches valid boundary options being rejected by server-side validation."""
    for value in values:
        payload = {
            "dataset_key": "dataset:1",
            "executor": "local",
            "base_model": "yolo26x.pt",
            "imgsz": 640,
            "epochs": 100,
            "batch": "auto",
            "device": "auto",
            "patience": 50,
            field: value,
        }
        request = TrainingRequest(**payload)
        assert getattr(request, field) == value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("training_log_tail_bytes", 0),
        ("training_log_tail_bytes", True),
        ("training_log_tail_bytes", 10.0),
        ("training_stop_timeout_seconds", 0),
        ("training_stop_timeout_seconds", True),
        ("training_stop_timeout_seconds", float("nan")),
        ("training_stop_timeout_seconds", float("inf")),
    ],
)
def test_training_settings_require_positive_operational_limits(
    tmp_path: Path,
    field: str,
    value: object,
):
    """Catches zero limits disabling bounded log or graceful-stop behavior."""
    with pytest.raises(ValueError):
        make_settings(tmp_path, **{field: value})
