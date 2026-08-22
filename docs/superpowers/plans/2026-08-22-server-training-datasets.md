# Selectable Server Training Datasets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let administrators define datasets beneath approved server roots, select one per local training job, reject unavailable base weights before queueing, expose safe Chinese failure diagnostics, and safely delete recognition/training tasks.

**Architecture:** Environment configuration supplies aliased absolute roots; APIs expose only aliases and normalized relative paths. Persisted dataset definitions are revalidated when a job is created, then converted into a contained immutable job-local YAML snapshot. Existing training scheduling remains unchanged, while worker/manager failures gain bounded structured diagnostics. Explicit delete services lock and re-read terminal jobs, remove contained artifacts before database rows, and protect registered-model provenance.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite/MySQL-compatible `Text`, React 19, TypeScript, TanStack Query, Vitest.

**Spec:** `docs/superpowers/specs/2026-08-22-local-model-training-design.md` (section `Addendum: server dataset selection and actionable failures`)

## Implementation Status (2026-08-22)

Tasks 1–7 are implemented on `master`: authorized server roots, safe directory browsing, persisted dataset definitions, per-job dataset snapshots, local weight availability checks, safe Chinese diagnostics, dataset management/selection UI, and terminal recognition/training job deletion in both API and console. Focused backend regression is `90 passed, 1 platform skip`; focused frontend regression is `20 passed`; TypeScript typecheck, Python compileall, diff-check, and the production frontend build pass. Per project scope, real YOLO training, full-suite/browser E2E, live MySQL, and deployment acceptance remain deferred.

## Global Constraints

- Only aliases and relative POSIX paths cross the API; never return or accept absolute server paths.
- `TRAINING_DATASET_ROOTS` uses semicolon-separated `alias=absolute-path` entries; the default is `default=<project>/datasets`.
- Reject traversal, drive/UNC/device paths, nested configured roots, and resolved symlink escapes.
- Dataset definitions require existing `train`, `val`, and `test` directories plus unique ordered class names.
- Persist JSON/status values as `Text`; migrations must work on SQLite and emit valid MySQL 8.0.13+ DDL.
- Existing `TRAINING_DATASET_YAML` remains a read-only legacy catalog entry only when contained by an authorized root.
- Keep stable public error codes, bounded logs, same-origin mutation protection, admin authorization, and path-free audits.
- Recognition owners may delete their own terminal jobs; administrators may delete any terminal recognition job and terminal unregistered training job.
- Active jobs return `job_active`; registered training provenance returns `model_registered`; cleanup failure retains the database row and returns `cleanup_failed`.
- Follow RED/GREEN TDD. Run focused tests, Python compile checks, frontend typecheck/build; defer real YOLO, full regression, browser E2E, and live MySQL.

---

### Task 1: Authorized roots and persisted dataset definitions

**Files:**
- Modify: `backend/core/config.py`
- Modify: `backend/db/models.py`
- Create: `migrations/versions/0007_training_datasets.py`
- Create: `backend/training/dataset_repository.py`
- Test: `tests/test_training_datasets.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces: `Settings.training_dataset_roots: tuple[tuple[str, Path], ...]`.
- Produces: ORM `TrainingDataset` and repository methods `list()`, `get(id)`, `create(values)`, `update(id, values)`, `disable(id)`, and `is_referenced(id)`.
- Produces: migration revision `0007`, with nullable `training_job.training_dataset_id` and a foreign key to `training_dataset.id`.

- [ ] **Step 1: Write configuration RED tests**

```python
def test_training_roots_parse_aliases_without_exposing_paths(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(); second.mkdir()
    settings = Settings(
        training_dataset_roots_raw=f"primary={first};customer={second}"
    )
    assert [alias for alias, _ in settings.training_dataset_roots] == ["primary", "customer"]

@pytest.mark.parametrize("value", ["missing", "a=relative", "../x=C:\\data", "a=C:\\x;a=C:\\y"])
def test_training_roots_reject_unsafe_configuration(value):
    with pytest.raises(ValueError):
        Settings(training_dataset_roots_raw=value)
```

- [ ] **Step 2: Run configuration tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_datasets.py -k roots -v`

Expected: FAIL because `training_dataset_roots` does not exist.

- [ ] **Step 3: Implement strict root parsing**

Add `training_dataset_roots_raw: str` plus an `init=False`
`training_dataset_roots` tuple. Parse and set the tuple in `__post_init__`, resolve
each existing absolute directory, validate aliases with
`^[a-z][a-z0-9_-]{0,31}$`, and reject duplicates/nesting. The default raw value is
`default=<resolved project datasets directory>`.

- [ ] **Step 4: Write migration/ORM/repository RED tests**

```python
def test_training_dataset_round_trip(repository):
    row = repository.create({
        "name": "PPE v2", "root_alias": "primary", "directory": "ppe-v2",
        "train_split": "images/train", "val_split": "images/val",
        "test_split": "images/test", "classes": ["person", "helmet", "no_helmet"],
        "created_by": 1,
    })
    assert row["classes"] == ["person", "helmet", "no_helmet"]
    assert repository.get(row["id"])["directory"] == "ppe-v2"
```

Assert migration head `0007`, `Text` for classes/status, uniqueness of `(root_alias, directory)`, the nullable job FK, SQLite upgrade→0006 downgrade→upgrade, and offline MySQL DDL.

- [ ] **Step 5: Run persistence tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_datasets.py tests/test_migrations.py -k "training_dataset or 0007" -v`

Expected: FAIL because the model, migration, and repository do not exist.

- [ ] **Step 6: Implement migration, ORM, and repository**

Use `Text` JSON for ordered classes and parenthesized server defaults. Make `disable()` idempotent. Convert uniqueness `IntegrityError` into a repository conflict without leaking SQL or values.

- [ ] **Step 7: Run Task 1 focused GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_datasets.py tests/test_migrations.py -k "roots or training_dataset or 0007" -v`

Expected: PASS; live-MySQL tests may skip only when `TEST_MYSQL_URL` is absent.

- [ ] **Step 8: Commit Task 1**

```powershell
git add backend/core/config.py backend/db/models.py backend/training/dataset_repository.py migrations/versions/0007_training_datasets.py tests/test_training_datasets.py tests/test_migrations.py
git commit -m "feat: persist selectable training datasets"
```

---

### Task 2: Safe directory browser and dataset validation service

**Files:**
- Create: `backend/training/dataset_service.py`
- Modify: `backend/training/storage.py`
- Modify: `backend/main.py`
- Test: `tests/test_training_dataset_service.py`

**Interfaces:**
- Produces: `DatasetService.list_roots() -> list[dict[str, str]]`.
- Produces: `DatasetService.list_directories(root_alias: str, relative: str) -> list[dict[str, str]]`.
- Produces: `create`, `update`, `disable`, and `catalog` methods returning safe dictionaries.
- Produces: `DatasetServiceError(code: str, status_code: int)` with stable codes `root_not_found`, `path_invalid`, `dataset_invalid`, `dataset_conflict`, and `dataset_in_use`.

- [ ] **Step 1: Write containment and browser RED tests**

```python
def test_browser_returns_only_relative_immediate_children(service, dataset_root):
    (dataset_root / "ppe" / "images").mkdir(parents=True)
    assert service.list_directories("primary", "") == [
        {"name": "ppe", "path": "ppe"}
    ]

@pytest.mark.parametrize("relative", ["../private", "C:/private", "//server/share"])
def test_browser_rejects_escaping_paths(service, relative):
    with pytest.raises(DatasetServiceError, match="path_invalid"):
        service.list_directories("primary", relative)
```

Add platform-gated directory symlink escape coverage and assert no returned value contains the absolute temporary root.

- [ ] **Step 2: Run browser tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_dataset_service.py -k browser -v`

Expected: FAIL because `DatasetService` does not exist.

- [ ] **Step 3: Implement root lookup and bounded browsing**

Resolve aliases from settings, normalize relative paths to POSIX form, use `resolve()` plus `relative_to(root)`, return at most 200 sorted immediate child directories, and skip/reject symlink escapes without exposing their targets.

- [ ] **Step 4: Write dataset CRUD validation RED tests**

```python
def test_create_validates_three_splits_and_classes(service, yolo_tree):
    created = service.create({
        "name": "Helmet site A", "root_alias": "primary", "directory": "site-a",
        "train_split": "images/train", "val_split": "images/val",
        "test_split": "images/test", "classes": ["person", "helmet", "no_helmet"],
    }, created_by=1)
    assert set(created) == {"id", "name", "key", "classes", "enabled", "available", "reason"}
```

Cover missing split, split outside dataset directory, duplicate/empty/overlong classes, conflicting directory, update revalidation, referenced disable versus unreferenced disable, and path-free errors/audits.

- [ ] **Step 5: Implement dataset CRUD and job-local YAML creation**

Add `TrainingStorage.write_dataset_snapshot(run_key, dataset)` using atomic temporary-file replacement. It writes only server-derived `path`, `train`, `val`, `test`, and ordered `names`; callers receive the contained snapshot path.

- [ ] **Step 6: Run Task 2 focused GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_dataset_service.py tests/test_training_storage.py -v`

Expected: PASS except the existing Windows symlink permission gate.

- [ ] **Step 7: Commit Task 2**

```powershell
git add backend/training/dataset_service.py backend/training/storage.py backend/main.py tests/test_training_dataset_service.py tests/test_training_storage.py
git commit -m "feat: browse authorized training data"
```

---

### Task 3: Dataset APIs, per-job snapshots, and model availability

**Files:**
- Modify: `backend/schemas.py`
- Modify: `backend/api/training_routes.py`
- Modify: `backend/training/catalog.py`
- Modify: `backend/training/service.py`
- Modify: `backend/training/repository.py`
- Modify: `backend/main.py`
- Test: `tests/test_training_api.py`
- Modify: `tests/test_training_service.py`

**Interfaces:**
- Changes: `TrainingRequest` gains required `dataset_key: str`.
- Changes: catalog returns `datasets: list[TrainingDatasetSummary]`, `default_dataset_key`, and model `available: bool`.
- Produces: the six admin dataset endpoints defined in the spec.
- Changes: job creation stores `training_dataset_id`, `dataset_key`, class snapshot, and the job-local YAML path only inside the private manifest.

- [ ] **Step 1: Write API/authorization RED tests**

Assert ordinary users receive 403, mutations require same-origin protection, directory/root DTOs contain no absolute paths, valid CRUD statuses are 200/201/204, referenced delete returns 409, and audit details contain IDs/aliases but no resolved path.

- [ ] **Step 2: Run API tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_api.py -k "dataset_root or dataset_director or dataset_crud" -v`

Expected: FAIL with missing routes/404.

- [ ] **Step 3: Implement schemas and routes**

Add strict request DTOs for name, root alias, relative directory/splits, ordered classes, and enabled state. Route all service failures through stable safe JSON responses and existing audit helpers.

- [ ] **Step 4: Write job snapshot and model availability RED tests**

```python
def test_create_uses_selected_dataset_snapshot(service, dataset):
    job = service.create(1, TrainingRequest(
        dataset_key=f"dataset:{dataset['id']}", executor="local",
        base_model="yolo11n.pt", imgsz=640, epochs=1,
        batch="1", device="cpu", patience=0,
    ), idempotency_key="job-key")
    assert job["dataset_key"] == f"dataset:{dataset['id']}"
    assert job["dataset_classes"] == ["person", "helmet", "no_helmet"]
```

Cover revalidation after directory removal, resume inheritance after definition update, legacy default compatibility, idempotency conflict across dataset keys, locally available root/cache weights, and pre-queue rejection of missing weights.

- [ ] **Step 5: Implement job selection and availability checks**

Resolve `dataset_key` through `DatasetService`, write the snapshot before manifest creation completes, and clean it on transaction failure. Locate allowlisted base weights only in project root/model cache; never trigger an implicit network download from job creation.

- [ ] **Step 6: Run Task 3 focused GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_api.py tests/test_training_service.py tests/test_training_catalog.py -v`

Expected: PASS with no network/GPU requirement.

- [ ] **Step 7: Commit Task 3**

```powershell
git add backend/schemas.py backend/api/training_routes.py backend/training/catalog.py backend/training/service.py backend/training/repository.py backend/main.py tests/test_training_api.py tests/test_training_service.py tests/test_training_catalog.py
git commit -m "feat: select datasets for training jobs"
```

---

### Task 4: Safe worker diagnostics and Chinese error mapping

**Files:**
- Modify: `training/worker.py`
- Modify: `backend/training/manager.py`
- Modify: `backend/training/storage.py`
- Modify: `frontend/src/admin/models/trainingApi.ts`
- Modify: `frontend/src/admin/models/TrainingJobsPanel.tsx`
- Test: `tests/test_training_worker.py`
- Test: `tests/test_training_manager.py`
- Modify: `frontend/src/admin/models/ModelsPage.test.tsx`

**Interfaces:**
- Produces: `append_safe_diagnostic(run_key, stage, code, exception_type=None, exit_code=None)`.
- Keeps: public result error codes unchanged.
- Produces: frontend `trainingErrorMessage(code, fallback)` mapping for all stable worker/manager codes.

- [ ] **Step 1: Write worker/manager diagnostic RED tests**

```python
def test_worker_records_safe_training_exception(run_dir):
    exit_code = run_manifest(manifest, yolo_cls=FailingYOLO)
    log = (run_dir / "training.log").read_text("utf-8")
    assert "stage=train code=training_failed exception=RuntimeError" in log
    assert "D:\\private" not in log
```

Cover setup/import failure, train/evaluate stages, child exit without result, bounded exception class, numeric exit code, UTF-8 byte limit, and absence of exception text/traceback/path.

- [ ] **Step 2: Run diagnostics tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_worker.py tests/test_training_manager.py -k diagnostic -v`

Expected: FAIL because structured diagnostics are absent.

- [ ] **Step 3: Implement bounded diagnostic writer**

Write one ASCII/UTF-8 key-value line with an allowlisted stage/code, sanitized exception class, and integer exit code. Reuse storage containment and append limits; logging failure must not replace the original stable result.

- [ ] **Step 4: Write frontend Chinese mapping RED tests**

Assert `weights_unavailable`, `cuda_oom`, `dataset_invalid`, `training_failed`, `evaluation_failed`, `interrupted`, and `worker_exit` render Chinese text while an unknown code uses a safe generic Chinese fallback.

- [ ] **Step 5: Implement frontend mapping and run GREEN**

Run: `$env:PATH="D:\free\.tools\node-runtime;$env:PATH"; npm --prefix frontend run test:run -- ModelsPage.test.tsx`

Expected: PASS with no React warnings.

- [ ] **Step 6: Run Task 4 backend GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_worker.py tests/test_training_manager.py -k "diagnostic or failure" -v`

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```powershell
git add training/worker.py backend/training/manager.py backend/training/storage.py frontend/src/admin/models/trainingApi.ts frontend/src/admin/models/TrainingJobsPanel.tsx tests/test_training_worker.py tests/test_training_manager.py frontend/src/admin/models/ModelsPage.test.tsx
git commit -m "fix: expose safe training failure diagnostics"
```

---

### Task 5: Dataset management and selection UI

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/admin/models/trainingApi.ts`
- Create: `frontend/src/admin/models/TrainingDatasetsPanel.tsx`
- Create: `frontend/src/admin/models/TrainingDatasetDialog.tsx`
- Modify: `frontend/src/admin/models/TrainingJobDialog.tsx`
- Modify: `frontend/src/admin/models/ModelsPage.tsx`
- Modify: `frontend/src/admin/admin.css`
- Modify: `frontend/src/admin/models/ModelsPage.test.tsx`

**Interfaces:**
- Consumes: Task 3 dataset roots/browser/CRUD/catalog DTOs.
- Produces: accessible dataset management dialog and required `dataset_key` selection in `TrainingJobDialog`.

- [ ] **Step 1: Write dataset management UI RED tests**

Cover opening the dataset dialog, choosing only returned root aliases/directories, browsing one level at a time, configuring three split directories/classes, displaying server validation errors in `role=alert`, and refreshing catalog after save/disable.

- [ ] **Step 2: Write training selection/model availability RED tests**

Assert the create dialog requires a dataset, defaults to `default_dataset_key`, disables unavailable datasets/models, selects the first available model instead of absent `yolo11m.pt`, and submits the exact selected `dataset_key`.

- [ ] **Step 3: Run frontend tests and verify RED**

Run: `$env:PATH="D:\free\.tools\node-runtime;$env:PATH"; npm --prefix frontend run test:run -- ModelsPage.test.tsx`

Expected: FAIL because dataset management and selection controls do not exist.

- [ ] **Step 4: Implement typed API and dataset components**

Use same-origin `api.request`, TanStack Query invalidation, existing `ModalDialog`/`ConfirmDialog`, keyboard-operable native selects/buttons, and in-dialog `role=alert`. Never render a server absolute path.

- [ ] **Step 5: Implement required dataset/model selection**

Initialize from `default_dataset_key` and the first `available` model. Keep submit disabled when no valid pair exists. Show classes and safe unavailable reasons next to the selected dataset.

- [ ] **Step 6: Run frontend GREEN and static gates**

Run: `$env:PATH="D:\free\.tools\node-runtime;$env:PATH"; npm --prefix frontend run test:run -- ModelsPage.test.tsx`

Run: `$env:PATH="D:\free\.tools\node-runtime;$env:PATH"; npm --prefix frontend run typecheck`

Run: `$env:PATH="D:\free\.tools\node-runtime;$env:PATH"; npm --prefix frontend run build`

Run: `.venv\Scripts\python.exe -m compileall -q backend training tests`

Run: `git diff --check`

Expected: all commands exit zero; do not run real training/full integration.

- [ ] **Step 7: Update configuration documentation**

Add `TRAINING_DATASET_ROOTS=default=D:\free\datasets` to `.env.example` using a non-secret example, and document aliases, directory selection, split/class requirements, local-weight availability, and deferred integration boundaries in `README.md`.

- [ ] **Step 8: Commit Task 5**

```powershell
git add frontend/src/api/types.ts frontend/src/admin/models/trainingApi.ts frontend/src/admin/models/TrainingDatasetsPanel.tsx frontend/src/admin/models/TrainingDatasetDialog.tsx frontend/src/admin/models/TrainingJobDialog.tsx frontend/src/admin/models/ModelsPage.tsx frontend/src/admin/admin.css frontend/src/admin/models/ModelsPage.test.tsx README.md .env.example
git commit -m "feat: manage server training datasets"
```

---

### Task 6: Safe recognition-job and training-job deletion APIs

**Files:**
- Modify: `backend/batch/repository.py`
- Modify: `backend/batch/service.py`
- Modify: `backend/batch/cleanup.py`
- Modify: `backend/api/batch_routes.py`
- Modify: `backend/training/repository.py`
- Modify: `backend/training/service.py`
- Modify: `backend/training/manager.py`
- Modify: `backend/api/training_routes.py`
- Test: `tests/test_batch_api.py`
- Modify: `tests/test_batch_cleanup.py`
- Modify: `tests/test_training_api.py`
- Modify: `tests/test_training_service.py`
- Modify: `tests/test_training_manager.py`

**Interfaces:**
- Produces: `BatchService.delete_job(job_id: int, user_id: int, is_admin: bool) -> None`.
- Produces: `TrainingService.delete(job_id: int) -> None`.
- Produces: `LocalTrainingManager.owns_job(job_id: int) -> bool`, synchronized by
  the manager condition lock and true for normal or quarantined active handles.
- Produces: `DELETE /api/batch-jobs/{id}` and `DELETE /api/training-jobs/{id}`, both returning HTTP 204.
- Keeps: parent-job-first database locking and stable errors `job_active`, `model_registered`, and `cleanup_failed`.

- [ ] **Step 1: Write recognition deletion RED tests**

```python
def test_owner_deletes_terminal_batch_job_and_all_files(client, completed_job):
    response = client.delete(
        f"/api/batch-jobs/{completed_job.id}",
        headers={"Origin": "http://testserver"},
        cookies=owner_cookie,
    )
    assert response.status_code == 204
    assert repository.get_job(completed_job.id) is None
    assert not source_path.exists()
    assert not annotated_path.exists()
```

Cover another owner receiving 404, admin deletion, queued/processing 409, missing task 404, missing files as idempotent success, a file-delete exception returning 503 with the row retained, database failure after file deletion retaining the row for retry, and safe audit fields.

- [ ] **Step 2: Run recognition tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_api.py tests/test_batch_cleanup.py -k delete -v`

Expected: FAIL because the DELETE route/service contract does not exist.

- [ ] **Step 3: Generalize the locked batch cleanup primitive**

Extract the state/file re-read portion of `cleanup_expired_job` into one private repository primitive used by both retention and explicit deletion. Explicit deletion omits the expiry predicate but enforces owner/admin and terminal status while holding the parent lock. Delete all exact stored source/annotated keys before deleting the row; keep processing images and their parent untouched.

- [ ] **Step 4: Implement recognition DELETE route and audit**

Map missing/not-owned to the existing non-enumerating 404, active to 409 `job_active`, and storage cleanup failure to 503 `cleanup_failed`. Require same-origin protection and record actor, job ID, outcome, and stable code only.

- [ ] **Step 5: Write training deletion RED tests**

```python
def test_admin_deletes_terminal_unregistered_training_job(service, completed_job):
    service.delete(completed_job["id"])
    assert repository.get_job(completed_job["id"]) is None
    assert not storage.run_path(completed_job["run_key"]).exists()
```

Cover admin-only API, queued/running/evaluating/stopping 409, each terminal status,
registered inactive/active model 409, normal/quarantined manager-owned job rejection,
contained run-directory cleanup, cleanup failure retaining the row, missing directory
idempotency, database rollback/retry, and path-free audit/errors.

- [ ] **Step 6: Run training tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_api.py tests/test_training_service.py tests/test_training_manager.py -k delete -v`

Expected: FAIL because training deletion is absent.

- [ ] **Step 7: Implement training deletion and route**

Add `owns_job()` without exposing the process handle. Add a repository deletion method
that locks/re-reads the job, queries `ModelVersion.training_job_id`, invokes a
contained run-directory cleanup callback while the parent lock is held, and deletes
only an allowed terminal unreferenced row after cleanup succeeds.
`TrainingService.delete` rejects manager-owned jobs before entering that operation.
Filesystem or repository exceptions become stable safe service errors.

- [ ] **Step 8: Run Task 6 focused GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_api.py tests/test_batch_cleanup.py tests/test_training_api.py tests/test_training_service.py tests/test_training_manager.py -k "delete or cleanup" -v`

Expected: PASS; no real model, GPU, or MySQL required.

- [ ] **Step 9: Commit Task 6**

```powershell
git add backend/batch/repository.py backend/batch/service.py backend/batch/cleanup.py backend/api/batch_routes.py backend/training/repository.py backend/training/service.py backend/training/manager.py backend/api/training_routes.py tests/test_batch_api.py tests/test_batch_cleanup.py tests/test_training_api.py tests/test_training_service.py tests/test_training_manager.py
git commit -m "feat: delete completed inference and training jobs"
```

---

### Task 7: Recognition and training deletion UI

**Files:**
- Modify: `frontend/src/batch/batchApi.ts`
- Modify: `frontend/src/batch/BatchPage.tsx`
- Modify: `frontend/src/batch/JobList.tsx`
- Modify: `frontend/src/batch/JobDetail.tsx`
- Modify: `frontend/src/batch/BatchPage.test.tsx`
- Modify: `frontend/src/admin/models/trainingApi.ts`
- Modify: `frontend/src/admin/models/TrainingJobsPanel.tsx`
- Modify: `frontend/src/admin/models/TrainingJobDetail.tsx`
- Modify: `frontend/src/admin/models/ModelsPage.test.tsx`
- Modify: `frontend/src/admin/admin.css`
- Modify: `frontend/src/batch/batch.css`

**Interfaces:**
- Consumes: Task 6 DELETE endpoints and stable error codes.
- Produces: `deleteJob(id)` in batch/training APIs and destructive confirmation flows in list/detail views.

- [ ] **Step 1: Write multi-image deletion UI RED tests**

Assert an owner sees delete only for their terminal task, an administrator sees it for any terminal task, active jobs have no delete action, confirmation is required, failure remains inside the dialog with `role=alert`, successful list deletion invalidates `batch-jobs`, and successful detail deletion navigates to `/batch`.

- [ ] **Step 2: Write training deletion UI RED tests**

Assert terminal unregistered jobs show delete, active and registered jobs do not, confirmation names the task ID, 409/503 errors appear in the dialog, success invalidates list/detail queries and closes detail, and stop/resume/register behavior remains unchanged.

- [ ] **Step 3: Run deletion UI tests and verify RED**

Run: `$env:PATH="D:\free\.tools\node-runtime;$env:PATH"; npm --prefix frontend run test:run -- BatchPage.test.tsx ModelsPage.test.tsx`

Expected: FAIL because neither frontend exposes a delete mutation or action.

- [ ] **Step 4: Implement typed DELETE calls and confirmation state**

Use `api.request<void>(path, { method: "DELETE" })`, existing `ConfirmDialog`, per-mutation error reset, and Chinese stable-code mapping. Do not optimistically remove data before the server confirms cleanup.

- [ ] **Step 5: Wire list/detail refresh and navigation**

After success invalidate the exact list/detail query prefixes. Batch detail navigates to `/batch`; training detail closes to its task list. Keep focus restoration and in-modal alert behavior from the shared dialog contract.

- [ ] **Step 6: Run Task 7 focused GREEN and static gates**

Run: `$env:PATH="D:\free\.tools\node-runtime;$env:PATH"; npm --prefix frontend run test:run -- BatchPage.test.tsx ModelsPage.test.tsx`

Run: `$env:PATH="D:\free\.tools\node-runtime;$env:PATH"; npm --prefix frontend run typecheck`

Run: `$env:PATH="D:\free\.tools\node-runtime;$env:PATH"; npm --prefix frontend run build`

Run: `.venv\Scripts\python.exe -m compileall -q backend training tests`

Run: `git diff --check`

Expected: all commands exit zero; real training/full integration remains deferred.

- [ ] **Step 7: Commit Task 7**

```powershell
git add frontend/src/batch/batchApi.ts frontend/src/batch/BatchPage.tsx frontend/src/batch/JobList.tsx frontend/src/batch/JobDetail.tsx frontend/src/batch/BatchPage.test.tsx frontend/src/admin/models/trainingApi.ts frontend/src/admin/models/TrainingJobsPanel.tsx frontend/src/admin/models/TrainingJobDetail.tsx frontend/src/admin/models/ModelsPage.test.tsx frontend/src/admin/admin.css frontend/src/batch/batch.css
git commit -m "feat: delete inference and training tasks"
```

---

## Deferred integration acceptance

- Real one-epoch YOLO training using a newly defined dataset.
- Full Python/frontend regression suites.
- Browser end-to-end directory selection and task creation.
- Live MySQL migration/CRUD/concurrency validation.
- Network download behavior for absent Ultralytics checkpoints.
- Browser end-to-end deletion, filesystem fault injection, and concurrent worker/delete races beyond focused deterministic tests.
