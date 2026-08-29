from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


CONFIG_SCHEMA_VERSION = 1
PROFILE_VERSION = 1

_METRICS = ("precision", "recall", "map50", "map50_95")


@dataclass(slots=True)
class TrainingConfigError(ValueError):
    code: str
    field_errors: dict[str, list[str]]

    def __post_init__(self) -> None:
        ValueError.__init__(self, self.code)


@dataclass(frozen=True, slots=True)
class _Field:
    kind: str
    default: object
    label: str
    help: str
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: tuple[object, ...] | None = None


_FIELDS = MappingProxyType(
    {
        "optimizer": _Field(
            "string", "auto", "优化器", "选择用于更新模型权重的优化算法。",
            choices=("auto", "SGD", "Adam", "AdamW"),
        ),
        "lr0": _Field("number", 0.01, "初始学习率", "训练开始时的学习率。", 0.000001, 0.1),
        "lrf": _Field("number", 0.01, "最终学习率比例", "最终学习率相对初始学习率的比例。", 0.01, 1.0),
        "momentum": _Field("number", 0.937, "动量", "优化器的动量或 Adam beta1。", 0.6, 0.98),
        "weight_decay": _Field("number", 0.0005, "权重衰减", "用于抑制过拟合的 L2 正则强度。", 0.0, 0.1),
        "warmup_epochs": _Field("number", 3.0, "预热轮数", "逐步提升学习率的训练轮数。", 0.0, 20.0),
        "warmup_momentum": _Field("number", 0.8, "预热动量", "预热阶段使用的初始动量。", 0.0, 0.95),
        "warmup_bias_lr": _Field("number", 0.1, "偏置预热学习率", "预热阶段偏置参数的学习率。", 0.0, 0.5),
        "cos_lr": _Field("boolean", False, "余弦学习率", "使用余弦曲线衰减学习率。"),
        "freeze": _Field("integer", 0, "冻结层数", "冻结模型前 N 层。", 0, 100),
        "cache": _Field("string", "false", "图片缓存", "选择关闭、磁盘或内存图片缓存。", choices=("false", "disk", "ram")),
        "workers": _Field("integer", 0, "加载进程数", "数据加载进程数量；Windows 本地默认使用 0。", 0, 16),
        "seed": _Field("integer", 0, "随机种子", "用于复现实验的随机种子。", 0, 2147483647),
        "deterministic": _Field("boolean", True, "确定性训练", "尽可能使用确定性算法复现实验。"),
        "amp": _Field("boolean", True, "混合精度", "在支持的硬件上启用自动混合精度。"),
        "hsv_h": _Field("number", 0.015, "色相增强", "随机调整图片色相的幅度。", 0.0, 0.1),
        "hsv_s": _Field("number", 0.7, "饱和度增强", "随机调整图片饱和度的幅度。", 0.0, 1.0),
        "hsv_v": _Field("number", 0.4, "亮度增强", "随机调整图片亮度的幅度。", 0.0, 1.0),
        "degrees": _Field("number", 0.0, "旋转角度", "随机旋转图片的最大角度。", 0.0, 45.0),
        "translate": _Field("number", 0.1, "平移比例", "随机平移图片的最大比例。", 0.0, 0.5),
        "scale": _Field("number", 0.5, "缩放比例", "随机缩放图片的最大比例。", 0.0, 0.9),
        "shear": _Field("number", 0.0, "剪切角度", "随机剪切变换的最大角度。", 0.0, 10.0),
        "perspective": _Field("number", 0.0, "透视比例", "随机透视变换的幅度。", 0.0, 0.001),
        "flipud": _Field("number", 0.0, "上下翻转概率", "随机上下翻转图片的概率。", 0.0, 1.0),
        "fliplr": _Field("number", 0.5, "左右翻转概率", "随机左右翻转图片的概率。", 0.0, 1.0),
        "mosaic": _Field("number", 1.0, "Mosaic 概率", "启用 Mosaic 拼图增强的概率。", 0.0, 1.0),
        "mixup": _Field("number", 0.0, "MixUp 概率", "启用 MixUp 图片混合的概率。", 0.0, 1.0),
        "close_mosaic": _Field("integer", 10, "关闭 Mosaic 轮数", "训练结束前关闭 Mosaic 的轮数。", 0, None),
        "box": _Field("number", 7.5, "边框损失权重", "边界框回归损失的权重。", 0.02, 20.0),
        "cls": _Field("number", 0.5, "分类损失权重", "类别预测损失的权重。", 0.01, 10.0),
        "dfl": _Field("number", 1.5, "DFL 损失权重", "分布焦点损失的权重。", 0.1, 10.0),
    }
)

_BALANCED = MappingProxyType({name: field.default for name, field in _FIELDS.items()})
_PROFILE_OVERRIDES = MappingProxyType(
    {
        "balanced": MappingProxyType({}),
        "small_object": MappingProxyType({"translate": 0.15, "scale": 0.7, "mixup": 0.05}),
        "high_recall": MappingProxyType({"optimizer": "AdamW", "lr0": 0.001, "cls": 0.75, "mixup": 0.1}),
        "stable_finetune": MappingProxyType(
            {"optimizer": "AdamW", "lr0": 0.001, "warmup_epochs": 1.0, "freeze": 10, "mosaic": 0.2, "scale": 0.25}
        ),
        "custom": MappingProxyType({}),
    }
)

_PROFILE_METADATA = (
    ("balanced", "均衡", "通用默认基线。", 640, 100, 50),
    ("small_object", "小目标", "适合远处或小尺寸目标。", 960, 100, 50),
    ("high_recall", "高召回", "优先降低重点类别漏检。", 640, 150, 75),
    ("stable_finetune", "稳定微调", "使用较低学习率微调已有领域模型。", 640, 50, 20),
    ("custom", "自定义", "从均衡配置开始自定义参数。", 640, 100, 50),
)

_GROUPS = (
    ("optimizer", "优化器与学习率", ("optimizer", "lr0", "lrf", "momentum", "weight_decay", "warmup_epochs", "warmup_momentum", "warmup_bias_lr", "cos_lr", "freeze")),
    ("augmentation", "数据增强", ("hsv_h", "hsv_s", "hsv_v", "degrees", "translate", "scale", "shear", "perspective", "flipud", "fliplr", "mosaic", "mixup", "close_mosaic")),
    ("runtime", "运行与复现", ("cache", "workers", "seed", "deterministic", "amp")),
    ("loss", "损失权重", ("box", "cls", "dfl")),
)


def _descriptor(field: _Field) -> dict[str, object]:
    result: dict[str, object] = {
        "type": field.kind,
        "label": field.label,
        "help": field.help,
        "default": field.default,
    }
    if field.minimum is not None:
        result["min"] = field.minimum
    if field.maximum is not None:
        result["max"] = field.maximum
    if field.choices is not None:
        result["choices"] = list(field.choices)
    return result


def catalog_schema() -> dict[str, object]:
    """Return a JSON-compatible schema used by API and catalog-driven forms."""
    generic = normalize_acceptance_config(None, classes=[])
    safety = normalize_acceptance_config(
        None,
        classes=["person", "helmet", "no_helmet"],
        template="safety_helmet",
    )
    return {
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "profile_version": PROFILE_VERSION,
        "training_profiles": [
            {
                "id": profile,
                "label": label,
                "description": description,
                "recommended": {"imgsz": imgsz, "epochs": epochs, "patience": patience},
                "overrides": dict(_PROFILE_OVERRIDES[profile]),
            }
            for profile, label, description, imgsz, epochs, patience in _PROFILE_METADATA
        ],
        "advanced_config_schema": {
            "groups": [
                {"id": group_id, "label": label, "fields": list(fields)}
                for group_id, label, fields in _GROUPS
            ],
            "fields": {name: _descriptor(field) for name, field in _FIELDS.items()},
        },
        "acceptance_schema": {
            "metrics": list(_METRICS),
            "templates": [
                {"id": "generic", "label": "通用目标检测", "required_classes": [], "config": generic},
                {
                    "id": "safety_helmet",
                    "label": "安全帽检测",
                    "required_classes": ["person", "helmet", "no_helmet"],
                    "config": safety,
                },
            ],
        },
    }


def _add_error(errors: dict[str, list[str]], field: str, reason: str) -> None:
    errors.setdefault(field, []).append(reason)


def _validate_field(name: str, value: object, spec: _Field, errors: dict[str, list[str]]) -> object:
    if spec.kind == "boolean":
        if type(value) is not bool:
            _add_error(errors, name, "invalid_type")
            return value
        return value
    if spec.kind == "integer":
        if type(value) is not int:
            _add_error(errors, name, "invalid_type")
            return value
        normalized: object = value
    elif spec.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _add_error(errors, name, "invalid_type")
            return value
        normalized = float(value)
        if not math.isfinite(normalized):
            _add_error(errors, name, "not_finite")
            return normalized
    elif spec.kind == "string":
        if not isinstance(value, str):
            _add_error(errors, name, "invalid_type")
            return value
        normalized = value
    else:  # pragma: no cover - frozen internal schema invariant
        raise RuntimeError(f"unsupported field kind: {spec.kind}")
    if spec.choices is not None and normalized not in spec.choices:
        _add_error(errors, name, "unsupported")
    if spec.minimum is not None and isinstance(normalized, (int, float)) and normalized < spec.minimum:
        _add_error(errors, name, "below_minimum")
    if spec.maximum is not None and isinstance(normalized, (int, float)) and normalized > spec.maximum:
        _add_error(errors, name, "above_maximum")
    return normalized


def resolve_training_config(
    *,
    mode: str,
    profile: str,
    overrides: Mapping[str, object] | None,
    epochs: int,
) -> dict[str, object]:
    """Expand and strictly validate an immutable advanced training snapshot."""
    if mode not in {"basic", "advanced"}:
        raise TrainingConfigError("training_config_invalid", {"mode": ["unsupported"]})
    selected = "balanced" if mode == "basic" else profile
    if selected not in _PROFILE_OVERRIDES:
        raise TrainingConfigError("training_profile_invalid", {"profile": ["unsupported"]})
    if overrides is not None and not isinstance(overrides, Mapping):
        raise TrainingConfigError("training_config_invalid", {"advanced_config": ["invalid_type"]})
    errors: dict[str, list[str]] = {}
    supplied = dict(overrides or {})
    for name in supplied.keys() - _FIELDS.keys():
        _add_error(errors, str(name), "unknown_field")
    values = {**_BALANCED, **_PROFILE_OVERRIDES[selected]}
    values.update({name: value for name, value in supplied.items() if name in _FIELDS})
    normalized = {
        name: _validate_field(name, values[name], spec, errors)
        for name, spec in _FIELDS.items()
    }
    if type(epochs) is not int or epochs < 1:
        _add_error(errors, "epochs", "invalid")
    elif "close_mosaic" not in supplied and normalized["close_mosaic"] > epochs:
        normalized["close_mosaic"] = epochs
    elif type(normalized["close_mosaic"]) is int and normalized["close_mosaic"] > epochs:
        _add_error(errors, "close_mosaic", "exceeds_epochs")
    if errors:
        raise TrainingConfigError("training_config_invalid", errors)
    return normalized


def _empty_metrics() -> dict[str, float | None]:
    return {metric: None for metric in _METRICS}


def _threshold(
    value: object,
    field: str,
    errors: dict[str, list[str]],
) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _add_error(errors, field, "invalid_type")
        return None
    result = float(value)
    if not math.isfinite(result):
        _add_error(errors, field, "not_finite")
        return None
    if not 0.0 <= result <= 1.0:
        _add_error(errors, field, "out_of_range")
        return None
    return result


def normalize_acceptance_config(
    value: Mapping[str, object] | None,
    *,
    classes: Sequence[str],
    template: str | None = None,
) -> dict[str, object]:
    """Normalize a generic or template-based acceptance rule to a stable shape."""
    if template not in {None, "generic", "safety_helmet"}:
        raise TrainingConfigError("training_config_invalid", {"template": ["unsupported"]})
    known_classes = set(classes)
    if template == "safety_helmet":
        required = {"person", "helmet", "no_helmet"}
        missing = sorted(required - known_classes)
        if missing:
            raise TrainingConfigError("acceptance_class_unknown", {"classes": [f"missing:{name}" for name in missing]})
    if value is not None and not isinstance(value, Mapping):
        raise TrainingConfigError("training_config_invalid", {"acceptance_config": ["invalid_type"]})

    if value is None:
        if template == "safety_helmet":
            return {
                "schema_version": CONFIG_SCHEMA_VERSION,
                "overall": {**_empty_metrics(), "map50": 0.85},
                "classes": {
                    "no_helmet": {**_empty_metrics(), "precision": 0.85, "recall": 0.9}
                },
            }
        return {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "overall": {**_empty_metrics(), "map50": 0.5},
            "classes": {},
        }

    errors: dict[str, list[str]] = {}
    raw = dict(value)
    for key in raw.keys() - {"schema_version", "overall", "classes"}:
        _add_error(errors, str(key), "unknown_field")
    schema_version = raw.get("schema_version", CONFIG_SCHEMA_VERSION)
    if type(schema_version) is not int or schema_version != CONFIG_SCHEMA_VERSION:
        _add_error(errors, "schema_version", "unsupported")
    raw_overall = raw.get("overall", {})
    if not isinstance(raw_overall, Mapping):
        _add_error(errors, "overall", "invalid_type")
        raw_overall = {}
    for metric in raw_overall.keys() - set(_METRICS):
        _add_error(errors, f"overall.{metric}", "unknown_field")
    overall = {
        metric: _threshold(raw_overall.get(metric), f"overall.{metric}", errors)
        for metric in _METRICS
    }
    raw_classes = raw.get("classes", {})
    if not isinstance(raw_classes, Mapping):
        _add_error(errors, "classes", "invalid_type")
        raw_classes = {}
    normalized_classes: dict[str, dict[str, float | None]] = {}
    unknown_class = False
    for raw_name, raw_metrics in raw_classes.items():
        name = str(raw_name)
        if not isinstance(raw_name, str) or raw_name not in known_classes:
            _add_error(errors, f"classes.{name}", "unknown_class")
            unknown_class = True
        if not isinstance(raw_metrics, Mapping):
            _add_error(errors, f"classes.{name}", "invalid_type")
            raw_metrics = {}
        for metric in raw_metrics.keys() - set(_METRICS):
            _add_error(errors, f"classes.{name}.{metric}", "unknown_field")
        normalized_classes[name] = {
            metric: _threshold(raw_metrics.get(metric), f"classes.{name}.{metric}", errors)
            for metric in _METRICS
        }
    enabled = any(value is not None for value in overall.values()) or any(
        value is not None
        for class_metrics in normalized_classes.values()
        for value in class_metrics.values()
    )
    if not enabled and not errors:
        _add_error(errors, "acceptance_config", "threshold_required")
    if errors:
        code = "acceptance_class_unknown" if unknown_class else "training_config_invalid"
        raise TrainingConfigError(code, errors)
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "overall": overall,
        "classes": normalized_classes,
    }


def _actual_metric(value: object) -> tuple[float | None, str | None]:
    if value is None:
        return None, "metric_missing"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "metric_invalid"
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        return None, "metric_invalid"
    return result, None


def evaluate_acceptance(
    metrics: Mapping[str, object],
    config: Mapping[str, object],
) -> tuple[bool, list[dict[str, object]]]:
    """Evaluate every enabled threshold and retain stable, user-facing reasons."""
    checks: list[dict[str, object]] = []
    overall_config = config.get("overall", {})
    metric_classes = metrics.get("classes", {})
    class_config = config.get("classes", {})

    def append_check(scope: str, class_name: str | None, metric: str, threshold: object, actual_value: object) -> None:
        if threshold is None:
            return
        actual, invalid_reason = _actual_metric(actual_value)
        passed = invalid_reason is None and actual is not None and actual >= float(threshold)
        reason = invalid_reason or ("threshold_met" if passed else "threshold_not_met")
        checks.append(
            {
                "scope": scope,
                "class_name": class_name,
                "metric": metric,
                "threshold": threshold,
                "actual": actual,
                "passed": passed,
                "reason": reason,
            }
        )

    if isinstance(overall_config, Mapping):
        for metric in _METRICS:
            append_check("overall", None, metric, overall_config.get(metric), metrics.get(metric))
    if isinstance(class_config, Mapping):
        for class_name in sorted(class_config):
            configured = class_config[class_name]
            actual_class = metric_classes.get(class_name, {}) if isinstance(metric_classes, Mapping) else {}
            for metric in _METRICS:
                threshold = configured.get(metric) if isinstance(configured, Mapping) else None
                actual_value = actual_class.get(metric) if isinstance(actual_class, Mapping) else None
                append_check("class", str(class_name), metric, threshold, actual_value)
    return all(bool(check["passed"]) for check in checks), checks
