# Training CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `training/train.py` into one Windows-safe command-line entry point with `train`, `resume`, and `validate` subcommands, then document and verify all three workflows.

**Architecture:** Keep one user-facing Python module while separating parsing, pure argument mapping, path validation, runtime setup, Ultralytics dispatch, and metric formatting into testable functions. Defer PyTorch and Ultralytics imports until after local paths have been validated so parser tests stay lightweight and bad input fails before CUDA initialization.

**Tech Stack:** Python 3.12, argparse, pathlib, PyTorch 2.x, Ultralytics 8.x, pytest 8/9, PowerShell on Windows.

## Global Constraints

- The entry point remains `python -m training.train`; a `train`, `resume`, or `validate` subcommand is required.
- New-training defaults are exactly: dataset `training/configs/construction-ppe-helmet.yaml`, model `yolo11n.pt`, epochs `50`, image size `512`, batch `4`, workers `0`, cache `false`, project `runs/detect`, patience `100`, and seed `0`.
- `--device auto` resolves to CUDA device `0` when available and `cpu` otherwise; an explicit device string passes through unchanged.
- Resume and validation require an existing local checkpoint; training and validation require an existing local dataset YAML.
- Named pretrained weights such as `yolo11n.pt` may be resolved by Ultralytics and are not rejected when absent locally.
- PyTorch and Ultralytics must not be imported by argument-parser tests.
- Runtime configuration stays under the repository's `data` directory.
- Dataset conversion, model export, backend inference, background-process management, and GUI work are outside this change.
- `D:\free` is not a Git repository. Do not initialize one; use the test checkpoints below instead of commit steps.

## File Map

- Modify `training/train.py`: parser, pure option builders, path checks, runtime configuration, dispatch, validation summary, and process exit behavior.
- Modify `tests/test_train_args.py`: parser, device, path, dispatch, metric, and failure tests with fake dependencies.
- Modify `README.md`: copy-paste usage, option reference, output locations, checkpoint guidance, and Windows troubleshooting.
- Read only `training/configs/construction-ppe-helmet.yaml`: the default dataset configuration used by commands and tests.

---

### Task 1: Define the subcommand parser and pure option mapping

**Files:**

- Modify: `training/train.py`
- Modify: `tests/test_train_args.py`

**Interfaces:**

- Produces: `build_parser() -> argparse.ArgumentParser`
- Produces: `parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace`
- Produces: `resolve_device(value: str, cuda_available: Callable[[], bool]) -> int | str`
- Produces: `train_kwargs(args, device) -> dict[str, object]`
- Produces: `resume_kwargs(args, device) -> dict[str, object]`
- Produces: `validate_kwargs(args, device) -> dict[str, object]`
- Preserves: `cache_value(value: str) -> bool | str`

- [ ] **Step 1: Record the current green baseline**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all existing tests pass before the CLI migration begins.

- [ ] **Step 2: Replace the old parser tests with failing subcommand tests**

Update `tests/test_train_args.py` to begin with these tests:

```python
import pytest

from training.train import (
    parse_args,
    resolve_device,
    resume_kwargs,
    train_kwargs,
    validate_kwargs,
)


def test_subcommand_is_required():
    with pytest.raises(SystemExit) as exc_info:
        parse_args([])
    assert exc_info.value.code == 2


def test_train_defaults_are_current_and_windows_safe():
    args = parse_args(["train"])
    assert args.command == "train"
    assert args.data == "training/configs/construction-ppe-helmet.yaml"
    assert args.model == "yolo11n.pt"
    assert args.epochs == 50
    assert args.imgsz == 512
    assert args.batch == 4
    assert args.device == "auto"
    assert args.workers == 0
    assert args.cache is False
    assert args.project == "runs/detect"
    assert args.name is None
    assert args.patience == 100
    assert args.seed == 0


def test_train_overrides_are_preserved():
    args = parse_args([
        "train", "--data", "custom.yaml", "--model", "custom.pt",
        "--epochs", "10", "--imgsz", "640", "--batch", "2",
        "--device", "cpu", "--workers", "2", "--cache", "ram",
        "--project", "output", "--name", "experiment",
        "--patience", "20", "--seed", "7",
    ])
    assert train_kwargs(args, "cpu") == {
        "data": "custom.yaml", "epochs": 10, "imgsz": 640, "batch": 2,
        "device": "cpu", "workers": 2, "cache": "ram",
        "project": "output", "name": "experiment", "patience": 20,
        "seed": 7,
    }


def test_resume_accepts_checkpoint_and_total_epoch_override():
    args = parse_args([
        "resume", "runs/job/weights/last.pt", "--epochs", "80",
        "--device", "0", "--workers", "1", "--cache", "disk",
    ])
    assert args.checkpoint == "runs/job/weights/last.pt"
    assert resume_kwargs(args, "0") == {
        "resume": True, "device": "0", "workers": 1,
        "cache": "disk", "epochs": 80,
    }


def test_resume_omits_epoch_override_by_default():
    args = parse_args(["resume", "last.pt"])
    assert "epochs" not in resume_kwargs(args, "cpu")


def test_validate_accepts_only_validation_or_test_split():
    args = parse_args(["validate", "best.pt", "--split", "test"])
    assert args.split == "test"
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["validate", "best.pt", "--split", "train"])
    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("requested", "available", "expected"),
    [("auto", True, 0), ("auto", False, "cpu"), ("cpu", True, "cpu"), ("0,1", True, "0,1")],
)
def test_device_resolution(requested, available, expected):
    assert resolve_device(requested, lambda: available) == expected
```

- [ ] **Step 3: Run the parser tests and confirm the intended failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_train_args.py -q
```

Expected: failures because `parse_args` does not accept an argument list and the new helper functions/subcommands do not exist.

- [ ] **Step 4: Implement the parser and pure helpers without runtime imports**

Refactor the top half of `training/train.py` around this structure:

```python
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence

DEFAULT_DATA = "training/configs/construction-ppe-helmet.yaml"


def add_device_options(parser: argparse.ArgumentParser, *, include_cache: bool) -> None:
    parser.add_argument("--device", default="auto", help="auto, cpu, 0, 0,1, etc.")
    parser.add_argument("--workers", type=int, default=0)
    if include_cache:
        parser.add_argument("--cache", type=cache_value, default=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train, resume, or validate a Safety Detect YOLO model")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train = subparsers.add_parser("train", help="start a new training run")
    train.add_argument("--data", default=DEFAULT_DATA)
    train.add_argument("--model", default="yolo11n.pt")
    train.add_argument("--epochs", type=int, default=50)
    train.add_argument("--imgsz", type=int, default=512)
    train.add_argument("--batch", type=int, default=4)
    train.add_argument("--project", default="runs/detect")
    train.add_argument("--name")
    train.add_argument("--patience", type=int, default=100)
    train.add_argument("--seed", type=int, default=0)
    add_device_options(train, include_cache=True)

    resume = subparsers.add_parser("resume", help="resume from a last.pt checkpoint")
    resume.add_argument("checkpoint")
    resume.add_argument("--epochs", type=int)
    add_device_options(resume, include_cache=True)

    validate = subparsers.add_parser("validate", help="evaluate a checkpoint")
    validate.add_argument("checkpoint")
    validate.add_argument("--data", default=DEFAULT_DATA)
    validate.add_argument("--split", choices=("val", "test"), default="val")
    validate.add_argument("--imgsz", type=int, default=512)
    validate.add_argument("--batch", type=int, default=4)
    add_device_options(validate, include_cache=False)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def resolve_device(value: str, cuda_available: Callable[[], bool]) -> int | str:
    if value != "auto":
        return value
    return 0 if cuda_available() else "cpu"


def train_kwargs(args: argparse.Namespace, device: int | str) -> dict[str, object]:
    values = {
        "data": args.data, "epochs": args.epochs, "imgsz": args.imgsz,
        "batch": args.batch, "device": device, "workers": args.workers,
        "cache": args.cache, "project": args.project,
        "patience": args.patience, "seed": args.seed,
    }
    if args.name is not None:
        values["name"] = args.name
    return values


def resume_kwargs(args: argparse.Namespace, device: int | str) -> dict[str, object]:
    values = {
        "resume": True, "device": device, "workers": args.workers,
        "cache": args.cache,
    }
    if args.epochs is not None:
        values["epochs"] = args.epochs
    return values


def validate_kwargs(args: argparse.Namespace, device: int | str) -> dict[str, object]:
    return {
        "data": args.data, "split": args.split, "imgsz": args.imgsz,
        "batch": args.batch, "device": device, "workers": args.workers,
    }
```

Keep these helpers pure: they must not touch the filesystem, import runtime dependencies, or mutate the namespace.

- [ ] **Step 5: Run the parser tests until they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_train_args.py -q
```

Expected: all Task 1 tests pass without importing PyTorch or Ultralytics.

- [ ] **Step 6: Inspect the Task 1 diff-equivalent files**

Run:

```powershell
Get-Content -Encoding UTF8 training\train.py
Get-Content -Encoding UTF8 tests\test_train_args.py
```

Expected: parser code contains no `torch` or `ultralytics` import at module scope, and every option in the design is represented once.

---

### Task 2: Add preflight checks, runtime dispatch, and validation metrics

**Files:**

- Modify: `training/train.py`
- Modify: `tests/test_train_args.py`

**Interfaces:**

- Consumes: `parse_args`, `resolve_device`, `train_kwargs`, `resume_kwargs`, `validate_kwargs` from Task 1.
- Produces: `configure_runtime(project_root: Path = PROJECT_ROOT) -> None`
- Produces: `validate_local_paths(args: argparse.Namespace) -> None`
- Produces: `format_validation_metrics(metrics: object) -> str`
- Produces: `execute(args, *, yolo_cls=None, cuda_available=None, project_root=PROJECT_ROOT) -> object`
- Produces: `main(argv: Sequence[str] | None = None, *, yolo_cls=None, cuda_available=None, project_root: Path = PROJECT_ROOT) -> int`

- [ ] **Step 1: Add fake-runtime and preflight tests**

Append this support code and the corresponding tests to `tests/test_train_args.py`:

```python
import os
from pathlib import Path
from types import SimpleNamespace

from training.train import configure_runtime, execute, format_validation_metrics


class FakeYOLO:
    instances = []

    def __init__(self, model):
        self.model = model
        self.train_calls = []
        self.val_calls = []
        self.__class__.instances.append(self)

    def train(self, **kwargs):
        self.train_calls.append(kwargs)
        return "trained"

    def val(self, **kwargs):
        self.val_calls.append(kwargs)
        return SimpleNamespace(
            names={0: "person", 1: "helmet", 2: "no_helmet"},
            box=SimpleNamespace(
                mp=0.65, mr=0.60, map50=0.70, map=0.36,
                ap50=[0.81, 0.92, 0.24], maps=[0.49, 0.50, 0.09],
            ),
        )


@pytest.fixture(autouse=True)
def clear_fake_yolo_instances():
    FakeYOLO.instances.clear()


def test_runtime_configuration_uses_project_local_directories(tmp_path, monkeypatch):
    monkeypatch.delenv("YOLO_CONFIG_DIR", raising=False)
    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    configure_runtime(tmp_path)
    assert os.environ["YOLO_CONFIG_DIR"] == str(tmp_path / "data")
    assert os.environ["MPLCONFIGDIR"] == str(tmp_path / "data" / "matplotlib")
    assert (tmp_path / "data" / "matplotlib").is_dir()


def test_missing_training_data_fails_before_model_load(tmp_path):
    args = parse_args(["train", "--data", str(tmp_path / "missing.yaml")])
    with pytest.raises(FileNotFoundError, match="dataset YAML"):
        execute(args, yolo_cls=FakeYOLO, cuda_available=lambda: False, project_root=tmp_path)
    assert FakeYOLO.instances == []


def test_train_dispatches_expected_arguments(tmp_path):
    data = tmp_path / "data.yaml"
    data.write_text("names: {0: person}", encoding="utf-8")
    args = parse_args(["train", "--data", str(data), "--name", "formal"])
    result = execute(args, yolo_cls=FakeYOLO, cuda_available=lambda: True, project_root=tmp_path)
    assert result == "trained"
    model = FakeYOLO.instances[0]
    assert model.model == "yolo11n.pt"
    assert model.train_calls[0]["device"] == 0
    assert model.train_calls[0]["name"] == "formal"
    assert Path(model.train_calls[0]["data"]).is_absolute()


def test_resume_requires_checkpoint_and_dispatches_resume(tmp_path):
    checkpoint = tmp_path / "last.pt"
    checkpoint.touch()
    args = parse_args(["resume", str(checkpoint), "--epochs", "80"])
    execute(args, yolo_cls=FakeYOLO, cuda_available=lambda: False, project_root=tmp_path)
    model = FakeYOLO.instances[0]
    assert Path(model.model) == checkpoint.resolve()
    assert model.train_calls == [{
        "resume": True, "device": "cpu", "workers": 0,
        "cache": False, "epochs": 80,
    }]


def test_validate_dispatches_and_returns_metrics(tmp_path):
    checkpoint = tmp_path / "best.pt"
    checkpoint.touch()
    data = tmp_path / "data.yaml"
    data.write_text("names: {0: person}", encoding="utf-8")
    args = parse_args([
        "validate", str(checkpoint), "--data", str(data), "--split", "test",
    ])
    metrics = execute(args, yolo_cls=FakeYOLO, cuda_available=lambda: False, project_root=tmp_path)
    model = FakeYOLO.instances[0]
    assert model.val_calls[0]["split"] == "test"
    assert model.val_calls[0]["device"] == "cpu"
    assert metrics.box.map50 == 0.70


def test_validation_summary_contains_overall_and_per_class_metrics():
    metrics = FakeYOLO("unused").val()
    summary = format_validation_metrics(metrics)
    assert "precision=0.6500" in summary
    assert "mAP50=0.7000" in summary
    assert "person: mAP50=0.8100, mAP50-95=0.4900" in summary
    assert "no_helmet: mAP50=0.2400, mAP50-95=0.0900" in summary
```

Add these exact process-exit tests so `main()` also remains free of real Ultralytics imports during unit testing:

```python
from training.train import main


def test_main_uses_argparse_exit_code_for_missing_checkpoint(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        main(
            ["resume", str(tmp_path / "missing.pt")],
            yolo_cls=FakeYOLO,
            cuda_available=lambda: False,
            project_root=tmp_path,
        )
    assert exc_info.value.code == 2


class FailingYOLO(FakeYOLO):
    def train(self, **kwargs):
        raise RuntimeError("CUDA failed")


def test_main_reports_runtime_failure(tmp_path, capsys):
    data = tmp_path / "data.yaml"
    data.write_text("names: {0: person}", encoding="utf-8")
    code = main(
        ["train", "--data", str(data)],
        yolo_cls=FailingYOLO,
        cuda_available=lambda: True,
        project_root=tmp_path,
    )
    assert code == 1
    assert "train failed: CUDA failed" in capsys.readouterr().err
```

- [ ] **Step 2: Run the new runtime tests and confirm they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_train_args.py -q
```

Expected: Task 1 tests pass and new tests fail because runtime configuration, preflight, execution, and metric formatting are not implemented.

- [ ] **Step 3: Implement local path validation before heavy imports**

Add these constants and helpers to `training/train.py`:

```python
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def configure_runtime(project_root: Path = PROJECT_ROOT) -> None:
    data_dir = project_root / "data"
    matplotlib_dir = data_dir / "matplotlib"
    matplotlib_dir.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(data_dir)
    os.environ["MPLCONFIGDIR"] = str(matplotlib_dir)


def required_file(value: str, label: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return str(path)


def validate_local_paths(args: argparse.Namespace) -> None:
    if args.command in {"train", "validate"}:
        args.data = required_file(args.data, "dataset YAML")
    if args.command in {"resume", "validate"}:
        args.checkpoint = required_file(args.checkpoint, "checkpoint")
```

Call `validate_local_paths(args)` before `configure_runtime()` and before importing either PyTorch or Ultralytics.

- [ ] **Step 4: Implement injected dispatch and metric formatting**

Use this dispatch shape in `training/train.py`:

```python
def execute(
    args: argparse.Namespace,
    *,
    yolo_cls=None,
    cuda_available=None,
    project_root: Path = PROJECT_ROOT,
):
    validate_local_paths(args)
    configure_runtime(project_root)
    if cuda_available is None:
        import torch
        cuda_available = torch.cuda.is_available
    if yolo_cls is None:
        from ultralytics import YOLO
        yolo_cls = YOLO

    device = resolve_device(args.device, cuda_available)
    if args.command == "train":
        return yolo_cls(args.model).train(**train_kwargs(args, device))
    if args.command == "resume":
        return yolo_cls(args.checkpoint).train(**resume_kwargs(args, device))
    metrics = yolo_cls(args.checkpoint).val(**validate_kwargs(args, device))
    print(format_validation_metrics(metrics))
    return metrics
```

`format_validation_metrics()` must read `metrics.box.mp`, `mr`, `map50`, `map`, `ap50`, and `maps`; emit values to four decimal places; and pair class indices with `metrics.names`. If per-class arrays are absent, it must still return the overall line rather than failing.

Use these implementations for metric formatting and process control:

```python
def format_validation_metrics(metrics: object) -> str:
    box = metrics.box
    lines = [
        "overall: "
        f"precision={float(box.mp):.4f}, recall={float(box.mr):.4f}, "
        f"mAP50={float(box.map50):.4f}, mAP50-95={float(box.map):.4f}"
    ]
    ap50 = getattr(box, "ap50", None)
    maps = getattr(box, "maps", None)
    if ap50 is None or maps is None:
        return "\n".join(lines)
    names = getattr(metrics, "names", {})
    for class_id, (map50, map5095) in enumerate(zip(ap50, maps)):
        name = names.get(class_id, str(class_id)) if hasattr(names, "get") else names[class_id]
        lines.append(f"{name}: mAP50={float(map50):.4f}, mAP50-95={float(map5095):.4f}")
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,
    *,
    yolo_cls=None,
    cuda_available=None,
    project_root: Path = PROJECT_ROOT,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        execute(
            args,
            yolo_cls=yolo_cls,
            cuda_available=cuda_available,
            project_root=project_root,
        )
    except FileNotFoundError as exc:
        parser.error(str(exc))
    except Exception as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the focused tests until they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_train_args.py -q
```

Expected: parser, preflight, fake dispatch, metric output, and exit-code tests all pass.

- [ ] **Step 6: Check every help surface without loading a model**

Run:

```powershell
.\.venv\Scripts\python.exe -m training.train --help
.\.venv\Scripts\python.exe -m training.train train --help
.\.venv\Scripts\python.exe -m training.train resume --help
.\.venv\Scripts\python.exe -m training.train validate --help
```

Expected: the root help lists all three subcommands; each command shows only its relevant positional arguments and options; no CUDA or model-loading output appears.

---

### Task 3: Replace the README training guide

**Files:**

- Modify: `README.md`

**Interfaces:**

- Consumes: the exact command contract implemented in Tasks 1 and 2.
- Produces: repository-root PowerShell commands for new training, resume, and test validation.

- [ ] **Step 1: Update the directory map**

In the `training/` tree, list all current training assets:

```text
training/
├── configs/
│   ├── construction-ppe-helmet.yaml
│   └── safety.yaml
├── prepare_construction_ppe.py
├── train.py                 # train / resume / validate
└── export.py
```

- [ ] **Step 2: Rewrite `## 数据与训练` around the trained helmet model**

Keep the class explanation but distinguish the current Construction-PPE model (`person`, `helmet`, `no_helmet`) from the future combined safety model (`fire`, `smoke`). Add these copy-paste PowerShell examples, explicitly stating they must be run from `D:\free` with the virtual environment installed:

```powershell
# Start a new 50-epoch run
.\.venv\Scripts\python.exe -m training.train train `
  --data training/configs/construction-ppe-helmet.yaml `
  --model yolo11n.pt --epochs 50 --imgsz 512 --batch 4 --device 0

# Resume an interrupted run; --epochs is the new total target
.\.venv\Scripts\python.exe -m training.train resume `
  runs/detect/train-3/weights/last.pt --epochs 80 --device 0

# Evaluate best.pt on the held-out test split
.\.venv\Scripts\python.exe -m training.train validate `
  runs/detect/train-3/weights/best.pt `
  --data training/configs/construction-ppe-helmet.yaml --split test --device 0
```

Explain that omitting `--device` selects GPU `0` when CUDA is available and CPU otherwise. State that the defaults remain `workers=0` and `cache=false` to avoid the observed Windows page-file/multiprocessing failure.

- [ ] **Step 3: Add an option and artifact reference**

Document these user decisions in a compact table:

| Need | Option or file | Guidance |
| --- | --- | --- |
| Smaller GPU memory use | `--batch 2` or `--batch 1` | Lower batch before lowering image size |
| Force CPU | `--device cpu` | Correct but much slower |
| Faster repeated reads | `--cache disk` | Prefer disk over RAM on this Windows host |
| Label a run | `--name helmet-v2` | Creates a recognizable run directory |
| Deploy/evaluate best quality | `weights/best.pt` | Best validation score during training |
| Recover exact run state | `weights/last.pt` | Optimizer and final epoch state for resume |
| Review curves | `results.csv`, `results.png` | Per-epoch losses and validation metrics |
| Review errors | `confusion_matrix.png`, `val_batch*_pred.jpg` | Class confusion and prediction examples |

Add troubleshooting entries:

- WinError 1455: keep `--workers 0 --cache false`, close orphan Python processes, and increase the Windows page file if the system remains memory constrained.
- CUDA out of memory: lower `--batch` first, then lower `--imgsz`; do not enable RAM cache as a GPU-memory fix.
- CUDA unavailable: run the existing environment check and use `--device cpu` until the PyTorch/CUDA installation is corrected.

Keep the existing ONNX/TensorRT export commands after the new CLI guide.

- [ ] **Step 4: Check README command consistency**

Run:

```powershell
rg -n "training\.train" README.md
rg -n -- "--workers|--cache|best\.pt|last\.pt|WinError 1455" README.md
```

Expected: no old train command without a subcommand remains, and all three workflows plus troubleshooting are discoverable.

---

### Task 4: Run regression and real-checkpoint verification

**Files:**

- Verify: `training/train.py`
- Verify: `tests/test_train_args.py`
- Verify: `README.md`

**Interfaces:**

- Consumes: completed CLI and documentation from Tasks 1-3.
- Produces: test evidence and one validation artifact generated through the new CLI.

- [ ] **Step 1: Run the full automated suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all training and existing backend/data tests pass.

- [ ] **Step 2: Verify invalid paths fail before Ultralytics startup**

Run:

```powershell
.\.venv\Scripts\python.exe -m training.train validate missing.pt --data missing.yaml
```

Expected: exit code `2` and a concise `dataset YAML not found` or `checkpoint not found` message, with no model download or CUDA initialization output.

- [ ] **Step 3: Validate the trained checkpoint through the new command**

Run:

```powershell
.\.venv\Scripts\python.exe -m training.train validate `
  runs/detect/train-3/weights/best.pt `
  --data training/configs/construction-ppe-helmet.yaml `
  --split test --imgsz 512 --batch 4 --device 0
```

Expected: Ultralytics completes the 140-image test split, the CLI prints overall and per-class metrics, and the overall mAP values are in the neighborhood of the previously measured `mAP50=0.6597` and `mAP50-95=0.3618`. Small numeric variation is acceptable; a missing summary or a large unexplained regression requires investigation.

- [ ] **Step 4: Perform the final plan-to-spec check**

Run:

```powershell
rg -n "def (build_parser|parse_args|resolve_device|configure_runtime|validate_local_paths|format_validation_metrics|execute|main)" training\train.py
rg -n "train|resume|validate" README.md tests\test_train_args.py
```

Expected: every acceptance criterion in `docs/superpowers/specs/2026-08-15-training-cli-design.md` maps to implemented code, tests, documentation, or the real-checkpoint validation above.
