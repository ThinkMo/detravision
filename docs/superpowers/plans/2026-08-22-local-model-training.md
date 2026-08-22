# Local Model Training Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add administrator-operated local YOLO11/YOLO26 training, held-out evaluation, candidate registration, and safe backend model activation to the existing model-management workflow.

**Architecture:** A database-backed `LocalTrainingManager` serially claims jobs and launches one shell-free Python child runner. The child writes atomic structured progress/results beneath a contained run directory; the manager persists safe status and metrics. Completed `best.pt` artifacts become inactive model versions, and `ModelActivationService` loads and warms a candidate before atomically updating the shared detector and active database row.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, Ultralytics/PyTorch, SQLite/MySQL, React 19, TypeScript, TanStack Query, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-local-model-training-design.md`

## Global Constraints

- Local execution is enabled; remote execution is returned by the catalog with `enabled=false` and cannot be submitted.
- The only accepted base model IDs are `yolo11{n,s,m,l,x}.pt` and `yolo26{n,s,m,l,x}.pt`.
- The server-configured dataset defaults to `training/configs/construction-ppe-helmet.yaml`; clients never provide a dataset path.
- Exactly one local training subprocess runs per application instance; SQLite is supported only for local single-instance use and production remains MySQL.
- Subprocess launch uses the current interpreter, an argument array, and `shell=False`; client values never become shell text or local paths.
- API DTOs and logs never expose absolute paths, environment variables, Python executable paths, commands, or raw tracebacks.
- Acceptance means overall `mAP50 >= 0.85`, `no_helmet precision >= 0.85`, and `no_helmet recall >= 0.90` on the held-out test split.
- Training results register inactive candidates; activation is always an explicit administrator action.
- Atlas `.om` conversion and remote execution are not part of this implementation.
- Preserve existing manual model registration, batch inference, real-time inference, auth, audit, and API response compatibility.

## File structure

New backend files:

- `backend/training/catalog.py`: fixed model catalog and parameter validation.
- `backend/training/repository.py`: training job persistence and atomic transitions.
- `backend/training/storage.py`: contained run directories and safe artifact reads.
- `backend/training/manager.py`: single-job scheduler and owned child-process lifecycle.
- `backend/training/service.py`: API-facing orchestration and candidate registration.
- `backend/training/__init__.py`: public training interfaces.
- `backend/model_activation.py`: candidate load/warm/persist/provider swap transaction boundary.
- `backend/api/training_routes.py`: administrator training endpoints and safe DTOs.
- `training/contracts.py`: JSON manifest/progress/result types and atomic JSON writes shared with the child.
- `training/worker.py`: Ultralytics child-process entry point.
- `migrations/versions/0005_local_training.py`: training persistence and model provenance.

New frontend files:

- `frontend/src/admin/models/TrainingJobsPanel.tsx`: list, polling, status, actions.
- `frontend/src/admin/models/TrainingJobDialog.tsx`: catalog-backed creation form.
- `frontend/src/admin/models/TrainingJobDetail.tsx`: metrics, logs, artifacts, acceptance.
- `frontend/src/admin/models/trainingApi.ts`: typed API functions.
- `frontend/src/admin/models/ModelsPage.test.tsx`: model/training behavior tests.

Existing files modified:

- `backend/db/models.py`, `backend/db/repositories.py`, `backend/schemas.py`, `backend/core/config.py`, `backend/main.py`, `backend/api/routes.py`.
- `backend/pipeline/runtime.py` only if the activation service needs an explicit model-signature setter.
- `frontend/src/admin/models/ModelsPage.tsx`, `frontend/src/api/types.ts`, `frontend/src/admin/admin.css`.
- `tests/test_migrations.py`, `tests/test_public_api_dtos.py`, `tests/test_authorization.py`, `tests/test_audit.py`, `README.md`, `.env.example`.

---

### Task 1: Training persistence and migration

**Files:**
- Create: `migrations/versions/0005_local_training.py`
- Modify: `backend/db/models.py`
- Create: `backend/training/repository.py`
- Create: `backend/training/__init__.py`
- Test: `tests/test_training_repository.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: `TrainingJob`, `training_job_dict(row)`, and `TrainingRepository` methods `create_job`, `list_jobs`, `get_job`, `claim_next`, `update_progress`, `request_stop`, `mark_terminal`, `recover_interrupted`, and `create_resume`.
- Consumes: existing `UtcDateTime`, `create_session_factory`, SQLite/MySQL migration conventions, and `User`/`ModelVersion` foreign keys.

- [ ] **Step 1: Write migration and ORM RED tests**

Add assertions that migration `0005` creates `training_job`, indexes status/created time, keeps JSON-like fields as `Text`, and adds nullable model provenance columns. Add model-level assertions such as:

```python
def test_training_job_uses_portable_text_status_and_metrics():
    assert isinstance(TrainingJob.__table__.c.status.type, Text)
    assert isinstance(TrainingJob.__table__.c.metrics_json.type, Text)
    assert ModelVersion.__table__.c.training_job_id.nullable is True
```

- [ ] **Step 2: Run migration RED tests**

Run: `pytest tests/test_migrations.py -k "training or model_version" -v`

Expected: FAIL because revision `0005`, `TrainingJob`, and provenance columns do not exist.

- [ ] **Step 3: Add migration and ORM model**

Create revision `0005` after `0004`. Use portable `Text` lifecycle values and parenthesized text defaults where existing MySQL compatibility requires them. Define status constraint:

```python
CheckConstraint(
    "status IN ('queued','running','evaluating','stopping','stopped','completed','failed','interrupted','cancelled')",
    name="status",
)
```

Store `batch` as `String(20)` so `auto` and numeric presets share one validated representation. Add a unique `idempotency_key`, `best_sha256`, `resume_from_id` self-FK, and nullable `training_job_id` on `ModelVersion`.

- [ ] **Step 4: Run migration GREEN tests including offline MySQL DDL**

Run: `pytest tests/test_migrations.py -v`

Run: `python -m backend.db.migrate --sql mysql`

Expected: migration tests PASS; emitted DDL reaches `0005` without MySQL-incompatible JSON defaults.

- [ ] **Step 5: Write repository RED tests**

Cover owner/admin-neutral persistence behavior with deterministic timestamps:

```python
first = repository.create_job(valid_job(created_by=admin.id))
second = repository.create_job(valid_job(created_by=admin.id))
assert repository.claim_next()["id"] == first["id"]
assert repository.claim_next() is None
repository.mark_terminal(first["id"], status="completed", metrics=metrics)
assert repository.claim_next()["id"] == second["id"]
```

Also cover idempotent stop requests, queued cancellation, restart recovery, resume lineage, bounded paging, and rollback when a progress update fails.

- [ ] **Step 6: Run repository RED tests**

Run: `pytest tests/test_training_repository.py -v`

Expected: collection FAIL because `TrainingRepository` does not exist.

- [ ] **Step 7: Implement `TrainingRepository` minimally**

Use `session_factory.begin()` for every transition. `claim_next()` selects the oldest queued job and conditionally updates `queued -> running`; use `SELECT FOR UPDATE` on MySQL and the repository's SQLite write-lock convention. Return JSON-ready records with `metrics_json` decoded to a dictionary and never include a run path.

- [ ] **Step 8: Run repository and migration GREEN tests**

Run: `pytest tests/test_training_repository.py tests/test_migrations.py -v`

Expected: PASS with only explicitly configured live-MySQL tests skipped.

- [ ] **Step 9: Commit Task 1**

```powershell
git add migrations/versions/0005_local_training.py backend/db/models.py backend/training/__init__.py backend/training/repository.py tests/test_training_repository.py tests/test_migrations.py
git commit -m "feat: persist local training jobs"
```

---

### Task 2: Catalog, configuration, and contained training storage

**Files:**
- Create: `backend/training/catalog.py`
- Create: `backend/training/storage.py`
- Modify: `backend/core/config.py`
- Modify: `.env.example`
- Test: `tests/test_training_catalog.py`
- Test: `tests/test_training_storage.py`

**Interfaces:**
- Produces: `MODEL_IDS`, `TrainingRequest`, `training_catalog(settings)`, `TrainingStorage.create_run()`, `resolve_artifact(run_key, kind)`, `tail_log(run_key, limit)`, and `scrub_log(text)`.
- Consumes: `Settings.training_dataset_yaml`, `training_run_dir`, `training_model_cache_dir`, `training_log_tail_bytes`, and `training_stop_timeout_seconds`.

- [ ] **Step 1: Write catalog and validation RED tests**

Assert exact model IDs and bounds:

```python
assert MODEL_IDS == tuple(
    f"yolo{series}{size}.pt"
    for series in ("11", "26")
    for size in ("n", "s", "m", "l", "x")
)
with pytest.raises(ValueError):
    TrainingRequest(base_model="../../evil.pt", imgsz=640, epochs=100, batch="auto", device="auto", patience=50)
```

Assert the catalog includes `{id: "remote", enabled: False}` and never returns the dataset absolute path.

- [ ] **Step 2: Write storage RED tests**

Cover random run keys, `resolve()` containment, allowlisted artifact kinds, byte-bounded log tails, and scrubbing of Windows/POSIX configured roots. Include drive, UNC, and traversal inputs.

- [ ] **Step 3: Run RED tests**

Run: `pytest tests/test_training_catalog.py tests/test_training_storage.py -v`

Expected: collection FAIL because catalog/storage modules do not exist.

- [ ] **Step 4: Add settings and fixed catalog**

Add frozen `Settings` fields with the design defaults. In `__post_init__`, require positive log/timeout values but do not require CUDA at application startup. `TrainingRequest` accepts only `executor="local"`, the ten model IDs, `imgsz in {640,960}`, `1 <= epochs <= 1000`, `batch in {"1","2","4","8","auto"}`, `device in {"auto","cpu","0"}`, and `0 <= patience <= 500`.

- [ ] **Step 5: Implement contained storage**

Use a random key such as `run-{secrets.token_hex(16)}` so the contained directory and manifest can be created before the queued database row becomes claimable. Map artifact kinds explicitly:

```python
ARTIFACTS = {
    "log": "training.log",
    "progress": "progress.json",
    "result": "result.json",
    "best": "weights/best.pt",
    "last": "weights/last.pt",
    "confusion_matrix": "confusion_matrix.png",
}
```

Reject any resolved path outside `training_run_dir`; do not accept filenames from API callers.

- [ ] **Step 6: Run GREEN tests**

Run: `pytest tests/test_training_catalog.py tests/test_training_storage.py -v`

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```powershell
git add backend/core/config.py backend/training/catalog.py backend/training/storage.py .env.example tests/test_training_catalog.py tests/test_training_storage.py
git commit -m "feat: define safe local training catalog"
```

---

### Task 3: Structured child runner and held-out evaluation

**Files:**
- Create: `training/contracts.py`
- Create: `training/worker.py`
- Modify: `training/train.py`
- Test: `tests/test_training_worker.py`
- Modify: `tests/test_train_args.py`

**Interfaces:**
- Produces: `TrainingManifest`, `TrainingProgress`, `TrainingResult`, `atomic_write_json(path, payload)`, `extract_metrics(metrics)`, `meets_acceptance(metrics)`, and `training.worker.main(argv)`.
- Consumes: a trusted manifest created by the manager containing fixed model ID, resolved dataset YAML, contained run directory, and validated numeric parameters.

- [ ] **Step 1: Write contract/metric RED tests**

Use fake Ultralytics metric objects to assert overall and per-class extraction, including `no_helmet`, and exact acceptance boundaries at 0.85/0.85/0.90. Assert atomic JSON leaves no temporary file after replacement.

- [ ] **Step 2: Write runner dispatch RED tests**

Inject a fake YOLO class and verify the runner calls:

```python
model.train(data=manifest.dataset_yaml, epochs=100, imgsz=640, batch=-1, device=0, workers=0, project=manifest.run_dir, name="train", exist_ok=True)
YOLO(best_path).val(data=manifest.dataset_yaml, split="test", imgsz=640, batch=-1, device=0, workers=0)
```

Verify `auto` device resolves through injected CUDA availability, `auto` batch maps to `-1`, progress is written through a callback, and failures write only stable codes such as `cuda_oom`, `dataset_invalid`, `weights_unavailable`, `evaluation_failed`, or `training_failed`.

- [ ] **Step 3: Run runner RED tests**

Run: `pytest tests/test_training_worker.py tests/test_train_args.py -v`

Expected: FAIL because contracts and worker do not exist.

- [ ] **Step 4: Implement contracts and metric extraction**

Use dataclasses with explicit `from_json` validation. `extract_metrics()` returns:

```python
{
    "precision": float(box.mp),
    "recall": float(box.mr),
    "map50": float(box.map50),
    "map50_95": float(box.map),
    "classes": {
        name: {"precision": p, "recall": r, "map50": ap50, "map50_95": ap}
    },
}
```

If Ultralytics does not expose per-class precision/recall, store `None` and mark acceptance false rather than inventing values.

- [ ] **Step 5: Implement the child runner**

Configure project-local YOLO/Matplotlib directories before heavy imports and change the child working directory to the configured model-cache directory before resolving a missing official weight. Register `on_train_epoch_end` to atomically write current epoch and available metrics. After training, locate `best.pt` beneath the fixed run directory, validate on `test`, compute its SHA-256 digest into `result.json`, and return zero only after a complete valid result.

- [ ] **Step 6: Refactor CLI to reuse shared helpers**

Keep existing CLI arguments and tests stable. Replace duplicated validation metric formatting inputs with `extract_metrics()` without changing documented train/resume/validate behavior.

- [ ] **Step 7: Run GREEN and CLI regression tests**

Run: `pytest tests/test_training_worker.py tests/test_train_args.py -v`

Expected: PASS without importing real CUDA/Ultralytics in unit tests.

- [ ] **Step 8: Commit Task 3**

```powershell
git add training/contracts.py training/worker.py training/train.py tests/test_training_worker.py tests/test_train_args.py
git commit -m "feat: run structured YOLO training jobs"
```

---

### Task 4: Local scheduler, stopping, recovery, and service orchestration

**Files:**
- Create: `backend/training/manager.py`
- Create: `backend/training/service.py`
- Modify: `backend/training/__init__.py`
- Test: `tests/test_training_manager.py`
- Test: `tests/test_training_service.py`

**Interfaces:**
- Produces: `LocalTrainingManager.start()`, `stop()`, `wake()`, `TrainingService.create`, `request_stop`, `resume`, `register_model`, `list_jobs`, and `get_job`.
- Consumes: `TrainingRepository`, `TrainingStorage`, `TrainingRequest`, current `sys.executable`, and an injectable `popen_factory`.

- [ ] **Step 1: Write scheduler RED tests with fake processes**

Use a controllable fake `Popen` to prove:

- only one process is active;
- launch is an argument list with `shell=False`;
- a queued second job starts only after the first becomes terminal;
- progress/result files update persistence;
- graceful stop escalates only for the owned live handle;
- shutdown wakes waits and terminates promptly;
- startup converts stale active statuses to `interrupted` and never signals stored PIDs.

- [ ] **Step 2: Run scheduler RED tests**

Run: `pytest tests/test_training_manager.py -v`

Expected: collection FAIL because `LocalTrainingManager` does not exist.

- [ ] **Step 3: Implement the scheduler loop**

Use `threading.Condition` rather than polling/busy-spin. Build the child call exactly as:

```python
[
    sys.executable,
    "-m",
    "training.worker",
    "--manifest",
    str(contained_manifest_path),
]
```

Redirect stdout/stderr to the contained log file. Keep the live `Popen` handle and job ID under one lock. Persist a bounded progress snapshot only when the atomic JSON file changes.

- [ ] **Step 4: Write service RED tests**

Cover idempotency keys, remote rejection, dataset unavailable, safe missing-job responses, resume requiring contained `last.pt`, and registration requiring completed status plus valid `best.pt`. Assert candidate `ModelVersion` is inactive and includes `training_job_id`, source, base model, image size, and metrics.

- [ ] **Step 5: Implement `TrainingService`**

The service validates requests through `TrainingRequest`, allocates a random run key, writes the manifest, and only then creates the queued DB job, so the manager can never claim an incomplete run. It wakes the manager after commit and cleans a newly created directory if persistence fails. Candidate registration recomputes SHA-256, compares it with `best_sha256` stored from evaluation, and calls the model repository with an internal path that is removed by `public_model()`.

- [ ] **Step 6: Run manager/service GREEN tests**

Run: `pytest tests/test_training_manager.py tests/test_training_service.py -v`

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```powershell
git add backend/training/manager.py backend/training/service.py backend/training/__init__.py tests/test_training_manager.py tests/test_training_service.py
git commit -m "feat: schedule local training processes"
```

---

### Task 5: Safe model activation and inference hot swap

**Files:**
- Create: `backend/model_activation.py`
- Modify: `backend/db/repositories.py`
- Modify: `backend/api/routes.py`
- Modify: `backend/pipeline/runtime.py`
- Test: `tests/test_model_activation.py`
- Modify: `tests/test_runtime_security.py`
- Modify: `tests/test_public_api_dtos.py`

**Interfaces:**
- Produces: `ModelActivationService.activate(model_id) -> dict`, `Repository.set_active_model(model_id)`, and stable exceptions `ModelNotFound`, `ModelLoadFailed`, `ModelWarmupFailed`, `ModelActivationConflict`.
- Consumes: `build_detector`, shared `DetectorProvider`, repository model records, and a small injectable warm-up image factory.

- [ ] **Step 1: Write activation RED tests**

Assert the candidate loads and predicts before DB activation, successful activation updates DB and provider, and each failure preserves both old states. Include concurrent activation serialization and assert no error/log contains the model path.

```python
with pytest.raises(ModelWarmupFailed):
    service.activate(candidate_id)
assert repository.get_active_model()["id"] == old_id
assert provider.predict_batch(image).model_version_id == old_id
```

- [ ] **Step 2: Run activation RED tests**

Run: `pytest tests/test_model_activation.py tests/test_runtime_security.py -v`

Expected: FAIL because the current route changes the database before runtime loading.

- [ ] **Step 3: Add repository transaction primitive**

Implement `set_active_model()` with one transaction and an ordered locking read on MySQL. Return the previous active model so a failed provider replacement can restore the DB state. Preserve existing `activate_model()` only as a compatibility wrapper if tests require it.

- [ ] **Step 4: Implement activation service and route mapping**

Use an instance lock. Build the detector, run one bounded zero-image prediction, update DB, then call `provider.replace()`. If replacement raises, restore the previous active row and retain the prior provider. Map stable failures to 404/409/503 without raw exception text. Update runtime model signature after a successful swap so reconciliation does not reload the same model.

- [ ] **Step 5: Run activation and API regression tests**

Run: `pytest tests/test_model_activation.py tests/test_runtime_security.py tests/test_public_api_dtos.py tests/test_api_compatibility.py -v`

Expected: PASS; public model DTO still omits `file_path` and now safely includes nullable provenance/metrics.

- [ ] **Step 6: Commit Task 5**

```powershell
git add backend/model_activation.py backend/db/repositories.py backend/api/routes.py backend/pipeline/runtime.py tests/test_model_activation.py tests/test_runtime_security.py tests/test_public_api_dtos.py tests/test_api_compatibility.py
git commit -m "fix: activate trained models safely"
```

---

### Task 6: Training API and application lifecycle

**Files:**
- Create: `backend/api/training_routes.py`
- Modify: `backend/schemas.py`
- Modify: `backend/main.py`
- Test: `tests/test_training_api.py`
- Modify: `tests/test_authorization.py`
- Modify: `tests/test_audit.py`
- Modify: `tests/test_app_startup.py`

**Interfaces:**
- Produces: the training API endpoints from the spec and application state fields `training_repository`, `training_storage`, `training_manager`, `training_service`, and `model_activation_service`.
- Consumes: Task 2 catalog, Task 4 service/manager, Task 5 activation service, auth dependencies, and audit service.

- [ ] **Step 1: Write API RED tests**

Cover catalog, create/list/detail, stop/resume/register, safe artifact serving, 401/403, same-origin rejection, stable validation errors, idempotency, and absence of local paths. Verify remote creation returns `422 executor_unavailable` while catalog returns it disabled.

- [ ] **Step 2: Write lifecycle RED tests**

Use injected fake manager/service objects to assert startup order is migrations/services/recovery/start and shutdown always stops the manager, including lifespan exceptions.

- [ ] **Step 3: Run API/lifecycle RED tests**

Run: `pytest tests/test_training_api.py tests/test_app_startup.py -k training -v`

Expected: 404/collection failures because the router and lifecycle services are absent.

- [ ] **Step 4: Implement schemas and routes**

Define strict request models and response DTO builders. Register endpoint handlers with `audited_admin` plus `audit_rejections`. Return only opaque artifact URLs:

```python
"confusion_matrix_url": f"/api/training-jobs/{job_id}/artifacts/confusion_matrix"
```

Use `FileResponse` only after the service returns a contained allowlisted artifact. Logs return scrubbed bounded UTF-8 text with replacement for undecodable bytes.

- [ ] **Step 5: Wire application lifecycle**

Build repository/storage/manager/service/activation once in lifespan, assign them to `app.state`, recover interrupted jobs before `manager.start()`, and stop the manager in `finally`. Include `training_router` before the SPA fallback.

- [ ] **Step 6: Run API, auth, audit, and startup GREEN tests**

Run: `pytest tests/test_training_api.py tests/test_authorization.py tests/test_audit.py tests/test_app_startup.py -v`

Expected: PASS with no new warnings.

- [ ] **Step 7: Commit Task 6**

```powershell
git add backend/api/training_routes.py backend/schemas.py backend/main.py tests/test_training_api.py tests/test_authorization.py tests/test_audit.py tests/test_app_startup.py
git commit -m "feat: expose local training workflow"
```

---

### Task 7: Model-management training UI

**Files:**
- Create: `frontend/src/admin/models/trainingApi.ts`
- Create: `frontend/src/admin/models/TrainingJobDialog.tsx`
- Create: `frontend/src/admin/models/TrainingJobsPanel.tsx`
- Create: `frontend/src/admin/models/TrainingJobDetail.tsx`
- Modify: `frontend/src/admin/models/ModelsPage.tsx`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/admin/admin.css`
- Create: `frontend/src/admin/models/ModelsPage.test.tsx`
- Modify: `frontend/src/admin/AdminPages.test.tsx`

**Interfaces:**
- Produces: typed `TrainingCatalog`, `TrainingJob`, `TrainingJobDetail`, create/stop/resume/register calls, and two model-page tabs.
- Consumes: existing `api.request`, TanStack Query, `ConfirmDialog`, `AdminNotice`, loading/error/empty components, and safe backend DTOs.

- [ ] **Step 1: Write frontend RED tests**

Mock catalog/jobs and verify:

- all ten model IDs render;
- remote is visible and disabled;
- default selection is YOLO11m/640;
- CPU with `m/l/x` requires confirmation;
- active jobs poll and terminal jobs do not;
- stop/resume/register errors appear in the active modal/section with `role="alert"`;
- acceptance and per-class metrics render;
- activating a registered candidate uses the existing confirmation dialog.

- [ ] **Step 2: Run frontend RED tests with project Node 24**

Run: `npm --prefix frontend run test:run -- src/admin/models/ModelsPage.test.tsx`

Expected: FAIL because training components and API types do not exist.

- [ ] **Step 3: Add typed API and creation dialog**

Keep server catalog as the source of options. Submit this shape only:

```ts
type TrainingJobCreate = {
  executor: "local";
  base_model: string;
  imgsz: 640 | 960;
  epochs: number;
  batch: "1" | "2" | "4" | "8" | "auto";
  device: "auto" | "cpu" | "0";
  patience: number;
};
```

Generate one idempotency key per dialog submission and reuse it only for that retry.

- [ ] **Step 4: Add job list/detail and polling**

Use `refetchInterval: job => activeStatuses.has(job.state.data?.status) ? 2000 : false`. Render progress with `role="progressbar"`, `aria-valuemin`, `aria-valuemax`, and `aria-valuenow`. Keep log text in a labeled scrollable `<pre>` without interpreting HTML.

- [ ] **Step 5: Integrate tabs into `ModelsPage`**

Retain manual registration and activation. Add “模型版本” and “训练任务”; successful registration invalidates both model and training queries. Use contained API artifact URLs for images and never render a path field.

- [ ] **Step 6: Run frontend GREEN tests, typecheck, and build**

Run: `npm --prefix frontend run test:run -- src/admin/models/ModelsPage.test.tsx src/admin/AdminPages.test.tsx`

Run: `npm --prefix frontend run typecheck`

Run: `npm --prefix frontend run build`

Expected: all PASS on Node 24; Vite emits `frontend/dist` without TypeScript errors or React warnings.

- [ ] **Step 7: Commit Task 7**

```powershell
git add frontend/src/admin/models frontend/src/api/types.ts frontend/src/admin/admin.css frontend/src/admin/AdminPages.test.tsx
git commit -m "feat: manage local training from the console"
```

---

### Task 8: Documentation and end-to-end acceptance

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Create: `tests/test_training_end_to_end.py`
- Modify: `docs/README.md` only if its directory map needs the new modules listed.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: operator documentation and an opt-in one-epoch real training acceptance path.

- [ ] **Step 1: Write opt-in integration test**

Gate it with `RUN_TRAINING_E2E=1`. Use a temporary SQLite database/run directory and configured dataset; select a locally available nano checkpoint and one epoch. Assert terminal completion, valid held-out metrics JSON, contained `best.pt`, candidate registration, and a fake-detector activation boundary. Default test runs skip without downloading weights.

- [ ] **Step 2: Run focused integration test in default mode**

Run: `pytest tests/test_training_end_to_end.py -v`

Expected: one explicit skip naming `RUN_TRAINING_E2E` when unset.

- [ ] **Step 3: Update operator documentation**

Document environment variables, dataset `test` requirement, ten supported model IDs, 640/960 trade-offs, queue/status semantics, stop/resume, test metric thresholds, candidate registration/activation/rollback, CPU/OOM/page-file guidance, and the boundaries that remote execution and Atlas `.om` conversion are disabled.

- [ ] **Step 4: Run backend focused regression**

Run:

```powershell
pytest tests/test_training_repository.py tests/test_training_catalog.py tests/test_training_storage.py tests/test_training_worker.py tests/test_training_manager.py tests/test_training_service.py tests/test_model_activation.py tests/test_training_api.py tests/test_train_args.py -v
```

Expected: PASS with no network or GPU requirement.

- [ ] **Step 5: Run complete SQLite/backend regression**

Run: `pytest -q`

Expected: all SQLite tests PASS; only existing live-MySQL/platform-gated cases and the opt-in training E2E may skip.

- [ ] **Step 6: Run complete frontend and static verification**

Run: `npm --prefix frontend run test:run`

Run: `npm --prefix frontend run typecheck`

Run: `npm --prefix frontend run build`

Run: `python -m compileall -q backend training tests`

Run: `git diff --check`

Expected: all commands exit zero with Node 24 and Python 3.12.

- [ ] **Step 7: Perform one real local smoke training when resources permit**

Run with `RUN_TRAINING_E2E=1` and a local nano checkpoint. Record model ID, dataset YAML, epoch count, input size, device, final status, `best.pt` existence, and test metrics. Do not claim the 0.85/0.90 customer acceptance thresholds from a one-epoch smoke run.

- [ ] **Step 8: Commit Task 8**

```powershell
git add README.md .env.example docs/README.md tests/test_training_end_to_end.py
git commit -m "docs: complete local training workflow"
```

---

## Final review checklist

- [ ] Confirm every training endpoint requires administrator authorization and same-origin protection.
- [ ] Confirm no public DTO, response, audit detail, or returned log contains a local model/dataset/run path.
- [ ] Confirm exact model whitelist and numeric bounds are enforced server-side.
- [ ] Confirm subprocess launch never uses a shell and only signals an owned live process handle.
- [ ] Confirm restart recovery does not signal a persisted PID.
- [ ] Confirm held-out `test` evaluation, not validation metrics, decides acceptance.
- [ ] Confirm candidate registration never activates automatically.
- [ ] Confirm failed loading/warm-up/persistence leaves both active DB model and shared detector unchanged.
- [ ] Confirm real-time and batch inference observe one model version per prediction through `DetectorProvider`.
- [ ] Confirm remote and Atlas options are described as unavailable rather than implemented.
- [ ] Confirm worktree is clean after final commit and report exact test results and skipped gates.
