# Training CLI Design

## Goal

Replace the current train-only command with one Python entry point that supports three explicit workflows:

- start a new YOLO training run;
- resume an interrupted run from a checkpoint;
- validate a trained checkpoint on the validation or test split.

The command must remain safe on Windows, work with the existing Construction-PPE helmet dataset, and be documented well enough that a user can run it without editing Python source.

## User-facing interface

The entry point remains `training/train.py` and is invoked as a module. A subcommand is required.

```powershell
# New training run
.\.venv\Scripts\python.exe -m training.train train `
  --data training/configs/construction-ppe-helmet.yaml `
  --model yolo11n.pt `
  --epochs 50 --imgsz 512 --batch 4 --device 0

# Resume an interrupted run
.\.venv\Scripts\python.exe -m training.train resume `
  runs/detect/train-3/weights/last.pt --device 0

# Evaluate the best checkpoint on the held-out test split
.\.venv\Scripts\python.exe -m training.train validate `
  runs/detect/train-3/weights/best.pt `
  --data training/configs/construction-ppe-helmet.yaml `
  --split test --device 0
```

The old command form without a subcommand is intentionally retired. Argparse will report that `train`, `resume`, or `validate` is required, and README examples will all use the new form.

## Command contract

### `train`

Starts a new run with `YOLO(model).train(...)`.

Options and defaults:

| Option | Default | Meaning |
| --- | --- | --- |
| `--data` | `training/configs/construction-ppe-helmet.yaml` | Dataset YAML |
| `--model` | `yolo11n.pt` | Ultralytics model name or local checkpoint |
| `--epochs` | `50` | Maximum epochs |
| `--imgsz` | `512` | Input image size |
| `--batch` | `4` | Batch size |
| `--device` | `auto` | Automatic CUDA/CPU choice, or explicit `cpu`, `0`, `0,1`, etc. |
| `--workers` | `0` | Data-loader workers; zero avoids the prior Windows spawn/page-file failure |
| `--cache` | `false` | `false`, `ram`, or `disk`; RAM cache stays off by default |
| `--project` | `runs/detect` | Output root |
| `--name` | unset | Optional run directory name |
| `--patience` | `100` | Early-stopping patience |
| `--seed` | `0` | Reproducibility seed |

### `resume`

Loads the positional checkpoint and calls `YOLO(checkpoint).train(resume=True, ...)`.

The checkpoint must exist locally. `--device`, `--workers`, and `--cache` use the same behavior and safe defaults as `train`. An optional `--epochs` sets a new total epoch target; when omitted, Ultralytics uses the saved run configuration. This supports both recovery from an interruption and intentionally extending a run.

### `validate`

Loads the positional checkpoint and calls `YOLO(checkpoint).val(...)`.

The checkpoint and dataset YAML must exist locally. Options include `--data`, `--split {val,test}`, `--imgsz`, `--batch`, `--device`, and `--workers`. Defaults match the training command, with `split=val`.

After validation, the command prints a compact summary containing overall precision, recall, mAP50, and mAP50-95, followed by per-class mAP50 and mAP50-95 values when Ultralytics provides them. Ultralytics' normal artifact output is retained.

## Internal structure

`training/train.py` remains a single user-facing script, but its responsibilities are separated into small functions:

1. `configure_runtime()` creates project-local Ultralytics and Matplotlib configuration directories and sets their environment variables before importing Ultralytics.
2. `build_parser()` defines the root parser and three subparsers.
3. `parse_args(argv=None)` provides a testable parsing boundary.
4. `resolve_device()` converts `auto` to GPU `0` when CUDA is available and to `cpu` otherwise; explicit device values pass through.
5. Argument-to-keyword helpers build the Ultralytics call for each subcommand without loading a model.
6. `run_train()`, `run_resume()`, and `run_validate()` perform the three workflows.
7. `main()` validates paths, dispatches by subcommand, and returns a nonzero exit status for invalid input or runtime failure.

Heavy imports (`torch`, `ultralytics`) remain inside runtime functions. Parser and option tests therefore do not initialize CUDA or require a GPU.

## Data and control flow

```text
command line
    -> parse required subcommand
    -> validate dataset/checkpoint paths
    -> configure local runtime directories
    -> resolve automatic or explicit device
    -> construct YOLO model
    -> train / resume / validate
    -> Ultralytics artifacts under runs/detect
    -> validation metric summary on stdout (validate only)
```

Dataset YAML paths and checkpoint paths are normalized relative to the current working directory. The README instructs users to invoke commands from the repository root, which also preserves the existing relative `path: construction-ppe-helmet` dataset behavior.

## Error handling

- Missing subcommands, unsupported values, and malformed numeric options are handled by argparse with exit code 2.
- A missing dataset YAML or checkpoint produces a short path-specific error before importing Ultralytics or starting CUDA work.
- A model such as `yolo11n.pt` is not required to exist locally because Ultralytics may resolve or download named pretrained weights.
- Training/runtime exceptions are not hidden. The CLI adds a concise failure prefix and exits nonzero while preserving enough exception context for diagnosis.
- The script never deletes or overwrites earlier run directories; Ultralytics' normal run naming behavior is used.

## Tests

Unit tests cover behavior without starting a real training job:

- the subcommand is required;
- all three subcommands parse correctly;
- `train` defaults to the helmet dataset, 50 epochs, 512 image size, batch 4, `workers=0`, and no cache;
- explicit worker/cache/device and run metadata overrides are preserved;
- `resume` accepts a checkpoint and optional total epoch override;
- `validate` restricts split values to `val` or `test`;
- missing dataset/checkpoint paths fail before a YOLO model is loaded;
- automatic device selection returns GPU `0` or `cpu` based on an injected CUDA availability check.

One lightweight dispatch test uses a fake YOLO class to verify that each command passes the intended keyword arguments. Existing non-training tests must continue to pass.

## Documentation changes

README's training section will be rewritten to include:

- prerequisites and virtual-environment activation;
- the three copy-paste PowerShell commands;
- a parameter reference focused on options users commonly change;
- where to find `best.pt`, `last.pt`, logs, plots, and validation results;
- guidance for choosing `best.pt` versus `last.pt`;
- Windows troubleshooting for WinError 1455, GPU out-of-memory, and forced CPU execution.

## Scope boundaries

This change does not convert/download datasets, export ONNX models, modify detection-service inference, launch training in the background, or add a graphical training interface. Dataset preparation stays in `training/prepare_construction_ppe.py`, and export stays in `training/export.py`.

## Acceptance criteria

- `python -m training.train --help` shows the three workflows.
- Each subcommand has useful command-specific help.
- New training, checkpoint resume, and val/test validation dispatch to the correct Ultralytics API.
- Windows-safe defaults are applied unless explicitly overridden.
- Invalid local paths fail quickly with a clear message.
- README usage matches the implemented CLI.
- The full automated test suite passes.
