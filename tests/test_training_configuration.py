from __future__ import annotations

import math
from contextlib import contextmanager

import pytest

from training.configuration import (
    CONFIG_SCHEMA_VERSION,
    PROFILE_VERSION,
    TrainingConfigError,
    catalog_schema,
    evaluate_acceptance,
    normalize_acceptance_config,
    resolve_training_config,
)


def test_basic_mode_expands_to_complete_balanced_snapshot():
    """Catches basic jobs persisting only partial or environment-dependent config."""
    result = resolve_training_config(
        mode="basic", profile="balanced", overrides=None, epochs=100
    )

    assert CONFIG_SCHEMA_VERSION == 1
    assert PROFILE_VERSION == 1
    assert result["optimizer"] == "auto"
    assert result["workers"] == 0
    assert result["deterministic"] is True
    assert set(result) == set(catalog_schema()["advanced_config_schema"]["fields"])


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (
            "small_object",
            {"translate": 0.15, "scale": 0.7, "mixup": 0.05},
        ),
        (
            "high_recall",
            {"optimizer": "AdamW", "lr0": 0.001, "cls": 0.75, "mixup": 0.1},
        ),
        (
            "stable_finetune",
            {
                "optimizer": "AdamW",
                "lr0": 0.001,
                "warmup_epochs": 1.0,
                "freeze": 10,
                "mosaic": 0.2,
                "scale": 0.25,
            },
        ),
        ("custom", {"optimizer": "auto", "lr0": 0.01, "mosaic": 1.0}),
    ],
)
def test_each_profile_expands_its_documented_differences(
    profile: str, expected: dict[str, object]
):
    """Catches profile changes silently altering the reproducible snapshot."""
    result = resolve_training_config(
        mode="advanced", profile=profile, overrides=None, epochs=100
    )

    assert {name: result[name] for name in expected} == expected


def test_advanced_overrides_are_applied_after_the_profile():
    """Catches user overrides being lost while expanding a preset."""
    result = resolve_training_config(
        mode="advanced",
        profile="high_recall",
        overrides={"lr0": 0.002, "workers": 4},
        epochs=100,
    )

    assert result["optimizer"] == "AdamW"
    assert result["lr0"] == 0.002
    assert result["workers"] == 4


def test_unknown_and_boolean_numeric_values_are_rejected_together():
    """Catches bool-as-number coercion and arbitrary Ultralytics arguments."""
    with pytest.raises(TrainingConfigError) as captured:
        resolve_training_config(
            mode="advanced",
            profile="custom",
            overrides={"lr0": True, "evil": 1},
            epochs=100,
        )

    assert captured.value.code == "training_config_invalid"
    assert set(captured.value.field_errors) == {"lr0", "evil"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("optimizer", "RMSprop"),
        ("cache", False),
        ("workers", 1.0),
        ("cos_lr", 1),
        ("lr0", math.nan),
        ("scale", math.inf),
        ("lr0", 0.0000009),
        ("momentum", 0.99),
        ("freeze", 101),
        ("perspective", 0.0011),
        ("box", 0.019),
    ],
)
def test_invalid_advanced_field_types_enums_and_ranges_are_rejected(
    field: str, value: object
):
    """Catches unsafe values reaching YOLO.train through a known field."""
    with pytest.raises(TrainingConfigError) as captured:
        resolve_training_config(
            mode="advanced",
            profile="custom",
            overrides={field: value},
            epochs=100,
        )

    assert captured.value.code == "training_config_invalid"
    assert field in captured.value.field_errors


def test_close_mosaic_cannot_exceed_task_epochs():
    """Catches a cross-field value that Ultralytics cannot apply as requested."""
    with pytest.raises(TrainingConfigError) as captured:
        resolve_training_config(
            mode="advanced",
            profile="custom",
            overrides={"close_mosaic": 11},
            epochs=10,
        )

    assert captured.value.field_errors == {"close_mosaic": ["exceeds_epochs"]}


@pytest.mark.parametrize(
    ("mode", "profile", "epochs"),
    [("basic", "balanced", 1), ("advanced", "high_recall", 3)],
)
def test_implicit_close_mosaic_default_is_clamped_for_short_smoke_jobs(
    mode: str, profile: str, epochs: int
):
    """Catches the ten-epoch preset default breaking existing short smoke jobs."""
    result = resolve_training_config(
        mode=mode, profile=profile, overrides=None, epochs=epochs
    )

    assert result["close_mosaic"] == epochs


def test_explicit_close_mosaic_override_above_epochs_remains_invalid():
    """Catches automatic default clamping accidentally hiding an invalid override."""
    with pytest.raises(TrainingConfigError) as captured:
        resolve_training_config(
            mode="advanced",
            profile="custom",
            overrides={"close_mosaic": 10},
            epochs=3,
        )

    assert captured.value.field_errors == {"close_mosaic": ["exceeds_epochs"]}


def test_training_config_error_can_propagate_through_a_context_manager():
    """Catches exception freezing blocking Python from updating its traceback."""

    @contextmanager
    def propagate():
        yield

    with pytest.raises(TrainingConfigError) as captured:
        with propagate():
            resolve_training_config(
                mode="advanced", profile="missing", overrides=None, epochs=100
            )

    assert captured.value.code == "training_profile_invalid"


@pytest.mark.parametrize(("mode", "profile", "code", "field"), [
    ("expert", "balanced", "training_config_invalid", "mode"),
    ("advanced", "missing", "training_profile_invalid", "profile"),
])
def test_unknown_modes_and_profiles_have_stable_error_codes(
    mode: str, profile: str, code: str, field: str
):
    """Catches invalid selection errors being collapsed into unsafe exceptions."""
    with pytest.raises(TrainingConfigError) as captured:
        resolve_training_config(mode=mode, profile=profile, overrides=None, epochs=100)

    assert captured.value.code == code
    assert field in captured.value.field_errors


def test_catalog_describes_every_field_and_profile_for_catalog_driven_forms():
    """Catches the UI needing a second hard-coded validation schema."""
    catalog = catalog_schema()

    assert set(catalog) == {
        "config_schema_version",
        "profile_version",
        "training_profiles",
        "advanced_config_schema",
        "acceptance_schema",
    }
    assert catalog["config_schema_version"] == 1
    assert catalog["profile_version"] == 1
    assert {profile["id"] for profile in catalog["training_profiles"]} == {
        "balanced",
        "small_object",
        "high_recall",
        "stable_finetune",
        "custom",
    }
    for profile in catalog["training_profiles"]:
        assert set(profile) == {"id", "label", "description", "recommended", "overrides"}
        assert set(profile["recommended"]) == {"imgsz", "epochs", "patience"}
    schema = catalog["advanced_config_schema"]
    assert set(schema) == {"groups", "fields"}
    grouped_fields = [field for group in schema["groups"] for field in group["fields"]]
    assert len(grouped_fields) == len(set(grouped_fields))
    assert set(grouped_fields) == set(schema["fields"])
    for name, descriptor in schema["fields"].items():
        assert descriptor["type"] in {"number", "integer", "boolean", "string"}, name
        assert descriptor["label"]
        assert descriptor["help"]
        assert "default" in descriptor
    acceptance = catalog["acceptance_schema"]
    assert acceptance["metrics"] == ["precision", "recall", "map50", "map50_95"]
    assert {template["id"] for template in acceptance["templates"]} == {
        "generic",
        "safety_helmet",
    }


def test_generic_acceptance_defaults_to_overall_map50_and_explicit_metric_keys():
    """Catches implicit thresholds and variable metric object shapes."""
    result = normalize_acceptance_config(None, classes=["fire", "smoke"])

    assert result == {
        "schema_version": 1,
        "overall": {
            "precision": None,
            "recall": None,
            "map50": 0.5,
            "map50_95": None,
        },
        "classes": {},
    }


def test_safety_template_requires_and_configures_explicit_helmet_classes():
    """Catches no-helmet acceptance being inferred from a generic person class."""
    result = normalize_acceptance_config(
        None, classes=["person", "helmet", "no_helmet"], template="safety_helmet"
    )

    assert result["overall"]["map50"] == 0.85
    assert result["classes"]["no_helmet"] == {
        "precision": 0.85,
        "recall": 0.9,
        "map50": None,
        "map50_95": None,
    }

    with pytest.raises(TrainingConfigError) as captured:
        normalize_acceptance_config(
            None, classes=["person", "helmet"], template="safety_helmet"
        )
    assert captured.value.code == "acceptance_class_unknown"
    assert "classes" in captured.value.field_errors


def test_acceptance_rejects_unknown_classes_unknown_fields_and_empty_thresholds():
    """Catches misspelled rules being silently ignored during evaluation."""
    with pytest.raises(TrainingConfigError) as captured:
        normalize_acceptance_config(
            {
                "schema_version": 1,
                "overall": {"precision": None, "unknown": 0.5},
                "classes": {"water": {"recall": 0.8}},
            },
            classes=["fire"],
        )
    assert captured.value.code == "acceptance_class_unknown"
    assert set(captured.value.field_errors) == {"overall.unknown", "classes.water"}

    with pytest.raises(TrainingConfigError) as empty:
        normalize_acceptance_config(
            {"schema_version": 1, "overall": {}, "classes": {}}, classes=["fire"]
        )
    assert empty.value.code == "training_config_invalid"
    assert "acceptance_config" in empty.value.field_errors


def test_acceptance_rejects_boolean_non_finite_and_out_of_range_thresholds():
    """Catches JSON values that cannot be meaningful probability thresholds."""
    with pytest.raises(TrainingConfigError) as captured:
        normalize_acceptance_config(
            {
                "overall": {"precision": True, "recall": math.nan},
                "classes": {"fire": {"map50": 1.1}},
            },
            classes=["fire"],
        )

    assert captured.value.code == "training_config_invalid"
    assert set(captured.value.field_errors) == {
        "overall.precision",
        "overall.recall",
        "classes.fire.map50",
    }


def test_acceptance_is_dynamic_and_reports_missing_class_metric():
    """Catches a missing per-class result accidentally passing its threshold."""
    config = normalize_acceptance_config(
        {
            "schema_version": 1,
            "overall": {"map50": 0.5},
            "classes": {"fire": {"recall": 0.8}},
        },
        classes=["fire"],
    )

    accepted, checks = evaluate_acceptance(
        {"map50": 0.7, "classes": {"fire": {"recall": None}}}, config
    )

    assert accepted is False
    assert checks == [
        {
            "scope": "overall",
            "class_name": None,
            "metric": "map50",
            "threshold": 0.5,
            "actual": 0.7,
            "passed": True,
            "reason": "threshold_met",
        },
        {
            "scope": "class",
            "class_name": "fire",
            "metric": "recall",
            "threshold": 0.8,
            "actual": None,
            "passed": False,
            "reason": "metric_missing",
        },
    ]


def test_acceptance_evaluates_all_enabled_thresholds_in_stable_order():
    """Catches short-circuiting that hides later failure reasons from the UI."""
    config = normalize_acceptance_config(
        {
            "overall": {"precision": 0.6, "map50": 0.7},
            "classes": {"smoke": {"recall": 0.8}, "fire": {"map50": 0.9}},
        },
        classes=["fire", "smoke"],
    )

    accepted, checks = evaluate_acceptance(
        {
            "precision": 0.5,
            "map50": 0.75,
            "classes": {
                "fire": {"map50": 0.95},
                "smoke": {"recall": 0.85},
            },
        },
        config,
    )

    assert accepted is False
    assert [(item["scope"], item["class_name"], item["metric"]) for item in checks] == [
        ("overall", None, "precision"),
        ("overall", None, "map50"),
        ("class", "fire", "map50"),
        ("class", "smoke", "recall"),
    ]
    assert [item["reason"] for item in checks] == [
        "threshold_not_met",
        "threshold_met",
        "threshold_met",
        "threshold_met",
    ]
