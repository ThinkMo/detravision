from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from training.contracts import (
    ERROR_MESSAGES,
    TrainingManifest,
    TrainingProgress,
    TrainingResult,
    atomic_write_json,
    extract_metrics,
)
from training.configuration import evaluate_acceptance
from training.train import PROJECT_ROOT, configure_runtime, resolve_device


_SAFE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_DIAGNOSTIC_STAGES = frozenset({"setup", "load", "train", "evaluate", "result"})


def _append_safe_diagnostic(
    run_dir: Path,
    *,
    stage: str,
    code: str,
    exception_type: str | None = None,
) -> None:
    if stage not in _DIAGNOSTIC_STAGES or code not in ERROR_MESSAGES:
        return
    fields = [f"stage={stage}", f"code={code}"]
    if exception_type and _SAFE_NAME.fullmatch(exception_type):
        fields.append(f"exception={exception_type}")
    line = ("training_diagnostic " + " ".join(fields) + "\n").encode("utf-8")[:256]
    try:
        with (run_dir / "training.log").open("ab") as stream:
            stream.write(line)
    except OSError:
        pass


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _contained(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("training artifact escaped run directory") from exc
    return resolved


def _batch(value: str) -> int:
    return -1 if value == "auto" else int(value)


def _evaluation_batch(requested_batch: int, trainer: object) -> int:
    if requested_batch > 0:
        return requested_batch
    actual_batch = getattr(trainer, "batch_size", 1)
    if isinstance(actual_batch, bool) or not isinstance(actual_batch, int):
        return 1
    return max(actual_batch, 1)


def _progress_metrics(raw_metrics: object) -> dict[str, float]:
    if not isinstance(raw_metrics, Mapping):
        return {}
    aliases = {
        "metrics/precision(B)": "precision",
        "metrics/recall(B)": "recall",
        "metrics/mAP50(B)": "map50",
        "metrics/mAP50-95(B)": "map50_95",
    }
    normalized: dict[str, float] = {}
    for source, target in aliases.items():
        value = raw_metrics.get(source)
        if value is None:
            continue
        try:
            normalized[target] = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_canonical_weights(run_dir: Path) -> Path:
    source_weights = _contained(run_dir / "train" / "weights", run_dir)
    source_best = _contained(source_weights / "best.pt", run_dir)
    if not source_best.is_file():
        raise FileNotFoundError("best checkpoint unavailable")
    destination_weights = _contained(run_dir / "weights", run_dir)
    destination_weights.mkdir(parents=True, exist_ok=True)
    destination_best = _contained(destination_weights / "best.pt", run_dir)
    shutil.copy2(source_best, destination_best)
    source_last = _contained(source_weights / "last.pt", run_dir)
    if source_last.is_file():
        shutil.copy2(source_last, _contained(destination_weights / "last.pt", run_dir))
    return destination_best


def _copy_results_curve(run_dir: Path) -> None:
    source = _contained(run_dir / "train" / "results.png", run_dir)
    if source.is_file():
        shutil.copy2(source, _contained(run_dir / "results.png", run_dir))


def _copy_confusion_matrix(raw_metrics: object, run_dir: Path) -> None:
    expected_save_dir = _contained(run_dir / "test", run_dir)
    raw_save_dir = getattr(raw_metrics, "save_dir", None)
    if raw_save_dir is not None:
        save_dir = _contained(Path(raw_save_dir), run_dir)
        if save_dir != expected_save_dir:
            raise ValueError("evaluation output directory is not fixed")
    else:
        save_dir = expected_save_dir
    source = _contained(save_dir / "confusion_matrix.png", save_dir)
    if not source.is_file():
        raise FileNotFoundError("confusion matrix unavailable")
    destination = _contained(run_dir / "confusion_matrix.png", run_dir)
    shutil.copy2(source, destination)


def _base_model_reference(
    manifest: TrainingManifest, project_root: Path, model_cache_dir: Path
) -> str:
    for candidate in (
        model_cache_dir / manifest.base_model,
        project_root / manifest.base_model,
    ):
        if candidate.is_file():
            return str(candidate.resolve())
    return manifest.base_model


def _write_failure(run_dir: Path, code: str) -> None:
    result = TrainingResult(
        status="failed",
        metrics=None,
        accepted=False,
        acceptance_checks=[],
        best_sha256=None,
        error_code=code,
        error_message=ERROR_MESSAGES[code],
    )
    atomic_write_json(run_dir / "result.json", result.to_json())


def _effective_config(manifest: TrainingManifest) -> dict[str, object]:
    return {
        "config_schema_version": manifest.config_schema_version,
        "base": {
            "base_model": manifest.base_model,
            "epochs": manifest.epochs,
            "imgsz": manifest.imgsz,
            "batch": manifest.batch,
            "device": manifest.device,
            "patience": manifest.patience,
        },
        "advanced_config": manifest.advanced_config,
    }


def _is_cuda_oom(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return "cuda" in text and ("out of memory" in text or "memory" in text)


def _is_dataset_error(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return any(
        marker in text for marker in ("dataset", "images not found", "data yaml")
    )


def _execute_manifest(
    manifest: TrainingManifest,
    *,
    yolo_cls=None,
    cuda_available: Callable[[], bool] | None = None,
    project_root: Path = PROJECT_ROOT,
) -> TrainingResult:
    run_dir = Path(manifest.run_dir).resolve()
    model_cache_dir = Path(manifest.model_cache_dir).resolve()
    dataset_yaml = Path(manifest.dataset_yaml).resolve()
    if not dataset_yaml.is_file():
        raise WorkerFailure("dataset_invalid", "setup")
    model_cache_dir.mkdir(parents=True, exist_ok=True)
    configure_runtime(project_root)

    with _working_directory(model_cache_dir):
        if cuda_available is None:
            import torch

            cuda_available = torch.cuda.is_available
        if yolo_cls is None:
            from ultralytics import YOLO

            yolo_cls = YOLO
        device = resolve_device(manifest.device, cuda_available)
        batch = _batch(manifest.batch)
        try:
            if manifest.resume_checkpoint is not None:
                resume_checkpoint = _contained(
                    Path(manifest.resume_checkpoint), run_dir
                )
                if not resume_checkpoint.is_file():
                    raise FileNotFoundError("resume checkpoint unavailable")
                model_reference = str(resume_checkpoint)
            else:
                model_reference = _base_model_reference(
                    manifest, project_root.resolve(), model_cache_dir
                )
            model = yolo_cls(model_reference)
        except Exception:
            raise WorkerFailure("weights_unavailable", "load")

        def on_train_epoch_end(trainer) -> None:
            progress = TrainingProgress(
                current_epoch=min(int(trainer.epoch) + 1, manifest.epochs),
                total_epochs=manifest.epochs,
                metrics=_progress_metrics(getattr(trainer, "metrics", None)),
            )
            atomic_write_json(run_dir / "progress.json", progress.to_json())

        model.add_callback("on_fit_epoch_end", on_train_epoch_end)
        try:
            train_arguments = dict(
                data=manifest.dataset_yaml,
                epochs=manifest.epochs,
                imgsz=manifest.imgsz,
                batch=batch,
                device=device,
                workers=0,
                project=manifest.run_dir,
                name="train",
                exist_ok=True,
                patience=manifest.patience,
            )
            if not manifest._legacy:
                train_arguments.update(manifest.advanced_config)
            if manifest.resume_checkpoint is not None:
                train_arguments["resume"] = True
                train_arguments["save_dir"] = str(
                    _contained(run_dir / "train", run_dir)
                )
            atomic_write_json(
                run_dir / "effective_config.json", _effective_config(manifest)
            )
            model.train(**train_arguments)
            best_path = _copy_canonical_weights(run_dir)
            _copy_results_curve(run_dir)
        except Exception as exc:
            if _is_cuda_oom(exc):
                code = "cuda_oom"
            elif _is_dataset_error(exc):
                code = "dataset_invalid"
            else:
                code = "training_failed"
            raise WorkerFailure(code, "train") from exc

        try:
            evaluation_model = yolo_cls(str(best_path))
            raw_metrics = evaluation_model.val(
                data=manifest.dataset_yaml,
                split="test",
                imgsz=manifest.imgsz,
                batch=_evaluation_batch(batch, model.trainer),
                device=device,
                workers=0,
                project=manifest.run_dir,
                name="test",
                exist_ok=True,
                plots=True,
            )
            metrics = extract_metrics(raw_metrics)
            _copy_confusion_matrix(raw_metrics, run_dir)
        except Exception as exc:
            code = "cuda_oom" if _is_cuda_oom(exc) else "evaluation_failed"
            raise WorkerFailure(code, "evaluate") from exc

        accepted, acceptance_checks = evaluate_acceptance(
            metrics, manifest.acceptance_config
        )
        result = TrainingResult(
            status="completed",
            metrics=metrics,
            accepted=accepted,
            acceptance_checks=acceptance_checks,
            best_sha256=_sha256(best_path),
            error_code=None,
            error_message=None,
        )
        return result


class WorkerFailure(Exception):
    def __init__(self, code: str, stage: str) -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage


def run_manifest(
    manifest: TrainingManifest,
    *,
    yolo_cls=None,
    cuda_available: Callable[[], bool] | None = None,
    project_root: Path = PROJECT_ROOT,
) -> int:
    try:
        run_dir = Path(manifest.run_dir).resolve()
        if not run_dir.is_dir():
            return 1
    except (OSError, RuntimeError, ValueError):
        return 1
    try:
        result = _execute_manifest(
            manifest,
            yolo_cls=yolo_cls,
            cuda_available=cuda_available,
            project_root=project_root,
        )
        atomic_write_json(run_dir / "result.json", result.to_json())
        return 0
    except WorkerFailure as exc:
        code = exc.code
        stage = exc.stage
        exception_type = (
            type(exc.__cause__).__name__ if exc.__cause__ is not None else None
        )
    except Exception as exc:
        code = "training_failed"
        stage = "setup"
        exception_type = type(exc).__name__
    _append_safe_diagnostic(
        run_dir, stage=stage, code=code, exception_type=exception_type
    )
    try:
        _write_failure(run_dir, code)
    except Exception:
        pass
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a structured local YOLO training job")
    parser.add_argument("--manifest", required=True, help="trusted training manifest")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    yolo_cls=None,
    cuda_available: Callable[[], bool] | None = None,
    project_root: Path = PROJECT_ROOT,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        manifest = TrainingManifest.from_json(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        print("training manifest is invalid", file=sys.stderr)
        return 1
    return run_manifest(
        manifest,
        yolo_cls=yolo_cls,
        cuda_available=cuda_available,
        project_root=project_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
