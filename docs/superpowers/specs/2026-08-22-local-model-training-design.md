# Local Model Training in Model Management

## Goal

Add an administrator-only local training workflow to the existing model-management page. Administrators can train any official YOLO11 or YOLO26 detection size (`n`, `s`, `m`, `l`, or `x`) against the server-configured helmet dataset, monitor progress, stop or resume a run, inspect held-out test metrics, and register the resulting `best.pt` as a selectable backend inference model.

This is an architectural extension of the existing training CLI and model registry. It must keep the FastAPI service responsive while training, preserve the currently active detector if a candidate cannot load, and avoid exposing server-local paths or raw process diagnostics through the API.

## Confirmed scope

- Local training is implemented now.
- A disabled “remote training” entry remains visible as a future extension point; no remote protocol or remote execution is implemented.
- The base-model catalog contains YOLO11 and YOLO26 in all five sizes: `n`, `s`, `m`, `l`, and `x`.
- The first release uses one server-configured YOLO dataset. The default is `training/configs/construction-ppe-helmet.yaml`; web upload, online annotation, and dataset CRUD are out of scope.
- Training runs in a dedicated child process. A single local scheduler permits only one running GPU training job and queues the rest.
- Completed training produces a candidate model version. An administrator must explicitly activate it.
- Acceptance is based on the held-out `test` split, not the confidence shown on individual boxes.
- Atlas 200I DK A2 conversion and `.om` execution are out of scope. The schema reserves deployment metadata so an Ascend export workflow can be added later.

## User experience

The model-management page gains two views: “模型版本” and “训练任务”. The existing manual registration path remains available.

### Create a training task

The administrator opens “新建训练” and chooses:

| Field | Allowed values | Default |
| --- | --- | --- |
| Execution target | Local; Remote (disabled, “暂未开放”) | Local |
| Series | YOLO11, YOLO26 | YOLO11 |
| Size | n, s, m, l, x | m |
| Input size | 640, 960 | 640 |
| Epochs | 1–1000 | 100 |
| Batch | 1, 2, 4, 8, auto | auto |
| Device | auto, cpu, 0 | auto |
| Patience | 0–500 | 50 |

The form shows guidance rather than blocking large models:

- `m + 640`: Atlas deployment baseline and the default balanced choice.
- `l + 640`: accuracy-oriented candidate.
- `l + 960`: small-object/image-recognition candidate with substantially lower inference speed.
- `x + 960`: comparison candidate; not recommended for real-time inference on Atlas 200I DK A2.
- CPU with `m/l/x` requires explicit confirmation because it may take an impractical amount of time.

Only fixed catalog identifiers are accepted by the API. The client cannot submit an arbitrary checkpoint path or command-line argument. The runner first uses a project-local matching weight; if it is absent, Ultralytics may resolve the fixed official model identifier into the project-local cache. A download or offline failure becomes a stable training error and does not create a model version.

### Task list and detail

The task list shows task ID, base model, input size, status, current/total epoch, progress, latest validation metrics, creator, creation time, and elapsed time. Active tasks poll every two seconds. Terminal tasks stop polling.

Statuses are:

```text
queued -> running -> evaluating -> completed
                  -> stopping -> stopped
                  -> failed
running/evaluating -> interrupted (application restart or lost process)
```

The detail view shows bounded recent log output, training/validation curves when available, the final metric summary, per-class metrics, confusion-matrix artifact, `best.pt`/`last.pt` availability, and stable failure guidance. It never returns an absolute filesystem path, full environment, raw command line, or secret.

Actions:

- queued: cancel;
- running/evaluating: stop;
- stopped/interrupted/failed with `last.pt`: resume;
- completed with `best.pt`: register candidate model;
- registered candidate: activate from the model-version list.

## Accuracy and acceptance

After training, the child process validates `best.pt` against `split=test` using the same input size and writes a structured result artifact. The server stores and displays:

- overall precision and recall;
- mAP50 and mAP50-95;
- per-class precision, recall, mAP50, and mAP50-95 when supplied by Ultralytics;
- explicit `no_helmet` metrics;
- the confusion-matrix artifact reference.

The acceptance policy is:

```text
overall mAP50 >= 0.85
no_helmet precision >= 0.85
no_helmet recall >= 0.90
```

`mAP50-95` is reported but is not required to reach 0.85. A run that misses the policy remains registrable for comparison, but the UI marks it “未达标” and requires confirmation before activation. The application never claims that choosing `l` or `x` guarantees the target; dataset quality and held-out test performance decide acceptance.

## Architecture

### Components

1. **Training API** validates administrator requests, returns safe DTOs, and records audit events.
2. **TrainingRepository** owns training-job persistence and atomic queue transitions.
3. **LocalTrainingManager** is a single application-owned scheduler thread. It claims at most one job, launches a child process, monitors structured artifacts, and performs recovery on startup.
4. **Training child runner** is a module entry point that owns Ultralytics imports, callbacks, training, held-out evaluation, and atomic structured progress/results files.
5. **Training storage** confines each run to a random server-generated directory below the configured training root.
6. **ModelActivationService** validates, loads, warms, persists, and atomically swaps a candidate detector while retaining the prior detector on failure.

The existing `training/train.py` CLI remains usable. Shared argument validation and metric extraction move into focused training modules rather than duplicating Ultralytics rules between the CLI and web worker.

### Process and data flow

```text
admin POST /api/training-jobs
  -> validate fixed model catalog and bounded parameters
  -> insert queued job
  -> LocalTrainingManager atomically claims it
  -> launch: current venv Python -m training.worker <opaque job token>
  -> worker reads server-created job manifest
  -> YOLO(base).train(...)
  -> atomic progress.json + bounded stdout/stderr log
  -> YOLO(best.pt).val(split="test")
  -> atomic result.json
  -> manager validates artifacts and marks completed/failed
  -> admin registers best.pt as inactive ModelVersion
  -> admin activates candidate through ModelActivationService
  -> existing DetectorProvider atomically replaces the detector
```

The subprocess receives a generated manifest path, not client-supplied shell text. It is launched with an argument array and `shell=False`. Output directories and artifact paths are derived from the job ID/random storage key and checked with `resolve()` containment before access.

## Persistence

A new migration adds `training_job` with the following logical fields:

- ownership/audit: `id`, `created_by`, `created_at`, `updated_at`;
- configuration: `executor`, `base_model`, `dataset_key`, `epochs`, `imgsz`, `batch`, `device`, `patience`;
- lifecycle: `status`, `current_epoch`, `total_epochs`, `pid`, `started_at`, `finished_at`, `stop_requested`;
- output references: opaque `run_key`, booleans for `best`/`last`/metrics artifacts;
- metrics: overall precision/recall/mAP values, `no_helmet` precision/recall, acceptance boolean, and structured per-class JSON stored as `Text`;
- safe error: stable `error_code` and bounded administrator-facing `error_message`.

`ModelVersion` gains nullable provenance/deployment fields sufficient to associate a registered model with a training job and future export artifacts: `training_job_id`, `source`, `base_model`, `imgsz`, `metrics_json`, and `deployment_runtime`. Existing rows remain valid and keep the current public DTO contract plus new nullable fields.

SQLite remains supported for local single-instance development. Production uses MySQL as already documented. Queue claiming and state changes use the repository's dialect-aware transaction patterns; no multi-process SQLite guarantee is claimed.

## Scheduling, stopping, and recovery

- One `LocalTrainingManager` exists per supported application instance.
- The manager performs an atomic queued-job claim before spawning.
- Only one training subprocess may be active. Additional requests remain queued.
- “Stop” records `stop_requested`, asks the process to terminate, waits a bounded grace period, and then force-terminates only the exact recorded child if necessary.
- A queued cancellation never starts a process.
- At application startup, jobs left in `running`, `evaluating`, or `stopping` become `interrupted`; generated artifacts are retained.
- Resume creates a new queued attempt linked to the prior job and uses its contained `last.pt`. It never mutates the historical job into a new run.
- Application shutdown requests a graceful child stop but does not recursively delete run data.

The PID is diagnostic metadata, not authority by itself. Before signaling, the manager must still own the live process handle for the current run; after a restart it marks the task interrupted rather than signaling an unrelated reused PID.

## Safe model registration and activation

Registering a completed task verifies that `best.pt` exists inside that task's run directory and that the file has not changed since evaluation. It creates an inactive model version without exposing its local path.

Activation changes from the current database-first behavior to a guarded service operation:

1. lock activation operations in-process;
2. read the candidate and validate path containment/provenance;
3. construct the detector and run a bounded synthetic warm-up prediction;
4. in one repository transaction, mark the candidate active and every other version inactive;
5. replace the shared `DetectorProvider` only after successful loading;
6. if persistence or replacement fails, keep or restore the previous database-active model and provider;
7. record a safe success/rejection audit event.

Real-time and batch inference already share `DetectorProvider`, so a successful swap applies to both. Existing in-flight prediction serialization is retained. Activation failure responses use stable codes and never contain local paths or Ultralytics tracebacks.

## API outline

All endpoints require an authenticated administrator and same-origin protection.

```text
GET    /api/training/catalog
GET    /api/training-jobs
POST   /api/training-jobs
GET    /api/training-jobs/{id}
POST   /api/training-jobs/{id}/stop
POST   /api/training-jobs/{id}/resume
POST   /api/training-jobs/{id}/register-model
GET    /api/training-jobs/{id}/artifacts/{kind}
```

The catalog returns the fixed model choices, the configured dataset summary, available execution targets, and local device capability. The remote target appears with `enabled=false`.

Create uses idempotency protection to prevent accidental duplicate jobs from a double click. List/detail are paginated or bounded. Log/artifact endpoints serve only allowlisted artifact kinds from contained paths.

## Configuration

New environment settings have safe local defaults:

```text
TRAINING_ENABLED=true
TRAINING_DATASET_YAML=training/configs/construction-ppe-helmet.yaml
TRAINING_RUN_DIR=data/training-runs
TRAINING_MODEL_CACHE_DIR=data/training-models
TRAINING_LOG_TAIL_BYTES=65536
TRAINING_STOP_TIMEOUT_SECONDS=15
```

The application validates the dataset YAML and writable roots before accepting a task. Missing CUDA does not prevent startup; the catalog reports CPU-only capability and the create form warns accordingly.

## Error handling and security

- Only the fixed YOLO model catalog and bounded numeric values reach the child runner.
- Dataset and output roots come from trusted server configuration, not request paths.
- Subprocess creation never uses a shell.
- API DTOs strip absolute paths, environment details, Python executable paths, and raw exception text.
- Logs returned by the API are byte-bounded and scrubbed for configured storage roots before response.
- Invalid/missing result artifacts fail closed and do not register a model.
- A failed download, CUDA OOM, invalid dataset, manual stop, child crash, and evaluation failure map to distinct stable error codes.
- All create, stop, resume, register, activate, and rejected operations are audited without model paths or credentials.

## Testing

Implementation follows TDD and uses fake child processes/fake YOLO classes for normal automated tests.

Backend tests cover:

- migration and ORM types/defaults on SQLite plus offline MySQL DDL;
- catalog whitelist and remote-disabled response;
- admin authorization and safe DTOs;
- parameter bounds and duplicate-submit protection;
- atomic single-job claiming and queue order;
- launch argument safety and path containment;
- progress/result ingestion and metric acceptance calculation;
- stop, forced-stop boundary, interrupted recovery, and resume lineage;
- candidate registration only from a valid contained `best.pt`;
- activation warm-up, atomic provider swap, and rollback on load/persistence failure;
- audit events and absence of local paths/raw diagnostics.

Frontend tests cover:

- model/version and training-task views;
- all YOLO11/YOLO26 sizes in the catalog;
- remote executor visible but disabled;
- large-model/CPU warnings;
- progress polling only for active states;
- stop/resume/register/activate success and failure feedback;
- metric/acceptance display and keyboard-accessible confirmation dialogs.

One opt-in integration test runs a one-epoch tiny training job when explicitly enabled. The default suite does not download weights or require CUDA. Final local acceptance includes the existing Python suite, frontend tests/typecheck/build, and a manual smoke run against the configured helmet dataset.

## Documentation

README gains:

- how to enable/disable local web training;
- supported base models and the 640/960 trade-off;
- dataset configuration and required `test` split;
- GPU/CPU prerequisites and Windows page-file/OOM guidance;
- task states, stopping, resuming, registering, activating, and rollback;
- where administrators see metrics without exposing server paths;
- explicit boundaries: remote training and Atlas `.om` conversion are not implemented.

## Acceptance criteria

- An administrator can queue YOLO11/YOLO26 `n/s/m/l/x` training against the configured helmet dataset.
- Training runs in a child process and does not block health, auth, inference, or ordinary API requests.
- Exactly one local GPU training job runs at a time and queued tasks progress in order.
- Progress, recent logs, stopping, interrupted recovery, and resume are usable from the model-management UI.
- Completed jobs are evaluated on the held-out test split and display overall plus `no_helmet` metrics and acceptance status.
- A valid `best.pt` can be registered as an inactive model version.
- Activation loads and warms the candidate before switching; failure preserves the old detector and database-active version.
- Real-time and batch inference use the newly activated model without a service restart.
- Remote training remains a clearly disabled placeholder.
- Automated tests do not require network access or a GPU, and the existing regression suites remain green.

## Addendum: server dataset selection and actionable failures

The initial implementation accepts one server-configured dataset. The management
console now needs to select a dataset per training job without allowing uploads or
arbitrary filesystem access. This addendum replaces the single-dataset catalog
assumption while retaining `TRAINING_DATASET_YAML` as the default selection for
backward compatibility.

### Dataset discovery and identity

- `TRAINING_DATASET_CONFIG_DIR` defaults to `training/configs` and is the only root
  searched for selectable `.yaml` and `.yml` definitions.
- `TRAINING_DATASET_ROOT` defaults to `datasets` and is the only root allowed for
  referenced training, validation, and test data.
- The server recursively scans bounded YAML files beneath the configuration root.
  Neither YAML definitions nor their resolved split paths may escape their
  respective roots through symlinks, Windows drive paths, UNC paths, or traversal
  segments.
- A dataset key is the normalized POSIX-style YAML path relative to
  `TRAINING_DATASET_CONFIG_DIR`. Clients never submit an absolute path.
- Discovery is bounded by YAML size and result count. An unreadable or invalid file
  is returned as unavailable with a stable reason; raw parser errors and local paths
  are not returned.
- A selectable YAML must define non-empty `train`, `val`, and `test` splits and a
  non-empty class mapping. Each split is resolved using Ultralytics dataset path
  semantics and must stay within `TRAINING_DATASET_ROOT`; referenced split
  directories/files must exist.
- `TRAINING_DATASET_YAML` remains the default dataset when it resolves within the
  configuration root and passes the same validation. It does not bypass containment
  checks.

The catalog dataset entry becomes a bounded list containing `key`, a display name
derived from the relative path, class names, split availability, and an availability
status. It contains no absolute paths or image counts that require an expensive
full-directory scan.

### Per-job selection and snapshot

`POST /api/training-jobs` requires a `dataset_key`. The service resolves that key
against the current discovery catalog and revalidates it in the same transaction
boundary used to create the job manifest. It snapshots the normalized key, class
list, and validated server YAML reference. A queued or running job therefore keeps
its original manifest if the catalog changes later; resume inherits the source
job's dataset snapshot and does not silently switch datasets.

The create dialog shows a required dataset selector before model parameters. Each
option displays its relative name and classes. Unavailable entries are disabled
with a Chinese reason, and no task can be submitted without one available dataset.

### Base-model availability

The model catalog reports whether each allowlisted checkpoint is already available
in the project root or controlled model cache. The UI labels unavailable weights
and defaults to the first locally available model. It does not promise that a
network download will succeed. Selecting or submitting an unavailable checkpoint
is rejected before a job is queued with the stable `weights_unavailable` code.

This avoids a known failure mode where a worker starts only to discover that GitHub
asset downloads are blocked. Administrators may provision an allowlisted checkpoint
on the server and refresh the catalog.

### Actionable and safe training diagnostics

Worker failures retain stable public error codes. Before writing a failed result,
the worker also appends one bounded structured diagnostic line to the task log with
only the execution stage, exception class, and stable code. It never includes raw
exception text, traceback source lines, environment values, or local paths.

If a child exits without a valid result, the manager records a similarly bounded
diagnostic containing the worker exit code and stage. The frontend maps stable codes
to Chinese messages and directs administrators to the bounded task log for the
diagnostic category. Existing English fallback messages are not shown as the primary
UI error.

### Focused verification

Implementation follows TDD and, per the current delivery boundary, runs focused
unit and component tests rather than real training or full integration suites.
Coverage includes discovery containment and limits, YAML/split validation, default
selection, per-job snapshotting, resume inheritance, unavailable model rejection,
safe worker/manager diagnostics, Chinese error mapping, dataset selection UI,
backend compile checks, frontend type checking, and the production frontend build.
Real YOLO training, live MySQL, and browser end-to-end validation remain deferred.

### Addendum acceptance criteria

- An administrator can choose one valid definition discovered under
  `training/configs/` whose data resolves under `datasets/` for a new local training
  task.
- Neither requests nor responses expose or accept an arbitrary server path.
- Invalid, escaping, or missing dataset configurations cannot be queued.
- The selected dataset and classes remain attributable to the job and its resume
  attempts even if catalog files later change.
- Missing local base weights are visible before submission and rejected without
  launching a worker.
- Worker startup/training failures provide a safe diagnostic category and a Chinese
  administrator-facing message without leaking paths or raw exceptions.
