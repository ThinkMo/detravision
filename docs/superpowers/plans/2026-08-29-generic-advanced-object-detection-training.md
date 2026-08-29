# Generic Advanced Object Detection Training Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe, reproducible advanced training for arbitrary rectangular-box object-detection datasets while preserving the existing basic workflow and helmet-specific compatibility.

**Architecture:** A shared Python configuration contract expands versioned presets into immutable snapshots, validates allowlisted overrides and evaluates generic acceptance rules. The API persists those snapshots, the isolated worker consumes the same contract, and the React console renders catalog-driven basic/advanced forms plus dynamic per-class results, cloning and comparison.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic, Ultralytics, React, TypeScript, TanStack Query, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-29-generic-advanced-object-detection-training-design.md`

## Global Constraints

- Only generic rectangular-box object detection is in scope; classification, segmentation, pose and rotated boxes are excluded.
- Never accept raw command lines, arbitrary YAML, arbitrary paths or unknown Ultralytics keys from the client.
- Configuration schema version is `1`; persisted task snapshots are immutable.
- Supported profiles are `balanced`, `small_object`, `high_recall`, `stable_finetune`, `custom` and migrated `legacy`.
- Basic mode must remain backward compatible and must still generate a complete balanced snapshot.
- Generic datasets may contain any valid category names; helmet mode uses explicit `person`, `helmet`, `no_helmet` classes and never infers `no_helmet` from helmet absence.
- SQLite remains the local runtime; migrations must also generate valid MySQL 8.0.13 offline DDL.
- Existing single-machine scheduler locking, path containment, artifact size limits and safe error messages remain in force.
- Do not stop the currently running local service or include `safety-detect-master-189c9e3.zip` in commits.

---

### Task 1: Versioned Advanced Configuration and Acceptance Contract

**Files:**
- Create: `training/configuration.py`
- Test: `tests/test_training_configuration.py`

**Interfaces:**
- Produces: `CONFIG_SCHEMA_VERSION: int`, `PROFILE_VERSION: int`, `catalog_schema() -> dict[str, object]`.
- Produces: `resolve_training_config(*, mode: str, profile: str, overrides: Mapping[str, object] | None, epochs: int) -> dict[str, object]`.
- Produces: `normalize_acceptance_config(value: Mapping[str, object] | None, *, classes: Sequence[str], template: str | None = None) -> dict[str, object]`.
- Produces: `evaluate_acceptance(metrics: Mapping[str, object], config: Mapping[str, object]) -> tuple[bool, list[dict[str, object]]]`.
- Raises: `TrainingConfigError(code: str, field_errors: dict[str, list[str]])` for strict type, range, enum, unknown-field and combination failures.

- [ ] **Step 1: Write failing contract tests**

```python
def test_basic_mode_expands_to_complete_balanced_snapshot():
    result = resolve_training_config(mode="basic", profile="balanced", overrides=None, epochs=100)
    assert result["optimizer"] == "auto"
    assert result["workers"] == 0
    assert result["deterministic"] is True
    assert set(result) == set(catalog_schema()["fields"])

def test_unknown_and_boolean_numeric_values_are_rejected():
    with pytest.raises(TrainingConfigError) as captured:
        resolve_training_config(mode="advanced", profile="custom", overrides={"lr0": True, "evil": 1}, epochs=100)
    assert captured.value.code == "training_config_invalid"
    assert set(captured.value.field_errors) == {"lr0", "evil"}

def test_acceptance_is_dynamic_and_reports_missing_class_metric():
    config = normalize_acceptance_config({"schema_version": 1, "overall": {"map50": .5}, "classes": {"fire": {"recall": .8}}}, classes=["fire"])
    accepted, checks = evaluate_acceptance({"map50": .7, "classes": {"fire": {"recall": None}}}, config)
    assert accepted is False
    assert checks[-1]["reason"] == "metric_missing"
```

- [ ] **Step 2: Run the focused tests and verify import failures**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_configuration.py -q`

Expected: FAIL because `training.configuration` does not exist.

- [ ] **Step 3: Implement frozen profile tables, field descriptors and strict normalization**

```python
@dataclass(frozen=True, slots=True)
class TrainingConfigError(ValueError):
    code: str
    field_errors: dict[str, list[str]]

def resolve_training_config(*, mode, profile, overrides, epochs):
    if mode not in {"basic", "advanced"}:
        raise TrainingConfigError("training_config_invalid", {"mode": ["unsupported"]})
    selected = "balanced" if mode == "basic" else profile
    resolved = {**BALANCED, **PROFILE_OVERRIDES[selected], **(overrides or {})}
    return _validate_advanced_config(resolved, epochs=epochs)
```

Implement every default, profile difference and bound exactly as specified. Serialize catalog field groups with type, default, enum/range and Chinese help text. Normalize acceptance to four explicit keys per metric set and evaluate every enabled threshold into stable check records containing `scope`, `class_name`, `metric`, `threshold`, `actual`, `passed`, `reason`.

- [ ] **Step 4: Run contract tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_configuration.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the contract**

```powershell
git add training/configuration.py tests/test_training_configuration.py
git commit -m "feat: add advanced training configuration contract"
```

### Task 2: Persist Immutable Training Configuration Snapshots

**Files:**
- Create: `migrations/versions/0009_advanced_training.py`
- Modify: `backend/db/models.py`
- Modify: `backend/training/repository.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_training_repository.py`

**Interfaces:**
- Consumes: canonical JSON-compatible dictionaries from Task 1.
- Produces on serialized jobs: `config_schema_version`, `training_profile`, `advanced_config`, `acceptance_config`.
- `TrainingRepository.create_resume()` copies all four fields without accepting replacements.

- [ ] **Step 1: Add failing ORM, migration and repository assertions**

```python
assert isinstance(TrainingJob.__table__.c.advanced_config.type, Text)
assert isinstance(TrainingJob.__table__.c.acceptance_config.type, Text)
assert row["training_profile"] == "high_recall"
assert row["advanced_config"]["optimizer"] == "AdamW"
assert resumed["advanced_config"] == source["advanced_config"]
```

Also assert 0008→0009 upgrade, downgrade and re-upgrade, non-null columns, legacy backfill, stable JSON serialization, and expected MySQL offline column DDL.

- [ ] **Step 2: Run migration and repository tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_migrations.py tests/test_training_repository.py -q`

Expected: FAIL on missing columns and fields.

- [ ] **Step 3: Implement migration and persistence**

```python
config_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
training_profile: Mapped[str] = mapped_column(String(30), nullable=False, default="balanced")
advanced_config: Mapped[str] = mapped_column(Text, nullable=False)
acceptance_config: Mapped[str] = mapped_column(Text, nullable=False)
```

Use batch alteration for SQLite. Add nullable columns, backfill historical rows with schema `1`, profile `legacy`, compact sorted JSON snapshots, then make them non-null. Parse JSON through Task 1 validators in repository serialization and never return mutable ORM JSON state.

- [ ] **Step 4: Run migration and repository tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_migrations.py tests/test_training_repository.py -q`

Expected: PASS.

- [ ] **Step 5: Commit persistence changes**

```powershell
git add migrations/versions/0009_advanced_training.py backend/db/models.py backend/training/repository.py tests/test_migrations.py tests/test_training_repository.py
git commit -m "feat: persist advanced training snapshots"
```

### Task 3: Catalog, Create, Clone and Resume APIs

**Files:**
- Modify: `backend/training/catalog.py`
- Modify: `backend/training/service.py`
- Modify: `backend/api/training_routes.py`
- Modify: `backend/schemas.py`
- Modify: `tests/test_training_catalog.py`
- Modify: `tests/test_training_service.py`
- Modify: `tests/test_training_api.py`

**Interfaces:**
- Consumes: Task 1 catalog and normalization functions; Task 2 repository fields.
- Produces: extended `GET /api/training/catalog`.
- Produces: `GET /api/training-jobs/{job_id}/clone-template`.
- Extends: `POST /api/training-jobs` with `mode`, `profile`, `advanced_config`, `acceptance_config`, `acceptance_template`.
- Returns field-safe errors with `training_config_invalid`, `training_profile_invalid`, `acceptance_class_unknown`.

- [ ] **Step 1: Write failing API and service tests**

```python
response = client.post("/api/training-jobs", headers={"Idempotency-Key": "advanced_123"}, json={**payload, "mode": "advanced", "profile": "high_recall", "advanced_config": {"lr0": .002}, "acceptance_config": {"overall": {"map50": .6}, "classes": {}}})
assert response.status_code == 201
assert response.json()["advanced_config"]["lr0"] == .002

clone = client.get(f"/api/training-jobs/{response.json()['id']}/clone-template")
assert clone.status_code == 200
assert "run_key" not in clone.json()
assert clone.json()["profile"] == "high_recall"
```

Cover basic request compatibility, safety template class requirements, unknown fields, idempotency conflict on snapshot differences, admin authorization, clone-template path/log/PID redaction, and resume snapshot identity.

- [ ] **Step 2: Run focused API tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_catalog.py tests/test_training_service.py tests/test_training_api.py -q`

Expected: FAIL on missing request fields and clone route.

- [ ] **Step 3: Extend request parsing and service orchestration**

```python
resolved = resolve_training_config(mode=request.mode, profile=request.profile, overrides=request.advanced_config, epochs=request.epochs)
acceptance = normalize_acceptance_config(request.acceptance_config, classes=dataset["classes"], template=request.acceptance_template)
```

Include both canonical dictionaries in manifest, repository values and idempotency comparison. Return a normalized clone request containing only public create fields. Map `TrainingConfigError` to HTTP 422 with stable `code` and `field_errors`.

- [ ] **Step 4: Run focused API tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_catalog.py tests/test_training_service.py tests/test_training_api.py tests/test_authorization.py -q`

Expected: PASS.

- [ ] **Step 5: Commit API changes**

```powershell
git add backend/training/catalog.py backend/training/service.py backend/api/training_routes.py backend/schemas.py tests/test_training_catalog.py tests/test_training_service.py tests/test_training_api.py
git commit -m "feat: expose advanced training APIs"
```

### Task 4: Worker Mapping, Dynamic Metrics and Generic Acceptance

**Files:**
- Modify: `training/contracts.py`
- Modify: `training/worker.py`
- Modify: `backend/training/manager.py`
- Modify: `backend/training/storage.py`
- Modify: `backend/api/training_routes.py`
- Modify: `tests/test_training_worker.py`
- Modify: `tests/test_training_manager.py`
- Modify: `tests/test_training_storage.py`
- Modify: `tests/test_training_api.py`

**Interfaces:**
- Consumes: manifest `config_schema_version`, `advanced_config`, `acceptance_config`.
- Produces: exact allowlisted `YOLO.train(**arguments)` mapping.
- Produces result: `accepted`, `acceptance_checks`, aggregate metrics and dynamic `classes` metrics.
- Keeps compatibility: `meets_acceptance(metrics, config=None)` delegates to the generic evaluator with a legacy helmet policy only for historical manifests.

- [ ] **Step 1: Write failing manifest, worker and result tests**

```python
assert captured_train_args["optimizer"] == "AdamW"
assert captured_train_args["lr0"] == .001
assert captured_train_args["mixup"] == .1
assert "run_dir" not in captured_train_args
assert result["acceptance_checks"][0]["metric"] == "map50"
assert result["metrics"]["classes"]["fire"]["recall"] == .81
```

Test corrupt/oversized JSON, unknown schema, path attempts, arbitrary keys, non-contiguous metric indexes mapped through model names, missing metrics, exact threshold boundaries, resume inheritance and safe error codes.
Also test that confusion matrix and results-curve artifacts are resolved only from the owned run directory and exposed through fixed artifact kinds without leaking their paths.

- [ ] **Step 2: Run worker tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_worker.py tests/test_training_manager.py -q`

Expected: FAIL on manifest fields and generic evaluation.

- [ ] **Step 3: Implement manifest parsing and allowlisted train mapping**

```python
train_arguments = {
    "data": str(dataset_yaml), "epochs": manifest.epochs, "imgsz": manifest.imgsz,
    "batch": batch, "device": device, "patience": manifest.patience,
    **manifest.advanced_config,
}
```

Build `manifest.advanced_config` only through Task 1 validation. Keep service-owned `project`, `name`, cache/model/run paths and resume checkpoint outside client-controlled configuration. Write the canonical effective configuration artifact atomically. Evaluate held-out metrics using the persisted acceptance snapshot and let manager verify result consistency against the same evaluator. Add fixed `confusion_matrix` and `results_curve` artifact mappings with existing containment and size checks; historical accepted values remain stored and are not recalculated.

- [ ] **Step 4: Run worker and training CLI regression tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_worker.py tests/test_training_manager.py tests/test_training_storage.py tests/test_training_api.py tests/test_train_args.py -q`

Expected: PASS.

- [ ] **Step 5: Commit worker changes**

```powershell
git add training/contracts.py training/worker.py backend/training/manager.py backend/training/storage.py backend/api/training_routes.py tests/test_training_worker.py tests/test_training_manager.py tests/test_training_storage.py tests/test_training_api.py
git commit -m "feat: execute generic advanced training configs"
```

### Task 5: Register Arbitrary-Class Trained Models Safely

**Files:**
- Modify: `backend/training/service.py`
- Modify: `backend/db/repositories.py`
- Modify: `backend/core/public_records.py`
- Modify: `tests/test_training_service.py`
- Modify: `tests/test_model_activation.py`

**Interfaces:**
- Consumes: completed job class/config/metric snapshots and `best.pt`.
- Produces: model provenance containing training profile, config summary, dataset digest, dynamic metrics and acceptance result.
- Preserves: transactional activation, provider warmup and rollback.

- [ ] **Step 1: Add failing registration tests**

```python
registered = service.register_model(job["id"], name="fire-v1")
assert registered["classes"] == ["fire", "smoke"]
assert registered["training_profile"] == "high_recall"
```

Mock checkpoint metadata and assert registration fails when class names or order differ from the immutable dataset snapshot. Assert arbitrary categories activate and warm up, while helmet categories keep the existing association runtime.

- [ ] **Step 2: Run registration and activation tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_service.py tests/test_model_activation.py tests/test_detector_provider.py -q`

Expected: FAIL on checkpoint class verification/provenance fields.

- [ ] **Step 3: Implement checkpoint metadata verification and provenance**

Load only trusted run artifacts using the existing detector/model loader boundary, compare normalized checkpoint names with `dataset_classes`, and reject mismatch as `model_classes_mismatch`. Do not deserialize user-supplied model files in this route. Extend public model fields without exposing paths.

- [ ] **Step 4: Run registration and runtime tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_service.py tests/test_model_activation.py tests/test_detector_provider.py tests/test_batch_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 5: Commit registration changes**

```powershell
git add backend/training/service.py backend/db/repositories.py backend/core/public_records.py tests/test_training_service.py tests/test_model_activation.py
git commit -m "feat: register generic trained models"
```

### Task 6: Catalog-Driven Basic and Advanced Training Dialog

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/admin/models/trainingApi.ts`
- Modify: `frontend/src/admin/models/TrainingJobDialog.tsx`
- Modify: `frontend/src/admin/models/ModelsPage.tsx`
- Modify: `frontend/src/admin/admin.css`
- Modify: `frontend/src/admin/models/ModelsPage.test.tsx`

**Interfaces:**
- Consumes: catalog field schema/profiles/templates from Task 3.
- Produces: typed `TrainingAdvancedConfig`, `TrainingAcceptanceConfig`, extended `TrainingJobCreate`.
- Produces: accessible basic/advanced form with grouped fields and final canonical summary.

- [ ] **Step 1: Add failing dialog tests**

```tsx
fireEvent.click(screen.getByRole("tab", { name: "高级模式" }));
fireEvent.change(screen.getByLabelText("训练预设"), { target: { value: "high_recall" } });
fireEvent.change(screen.getByLabelText("初始学习率"), { target: { value: "0.002" } });
fireEvent.click(screen.getByRole("button", { name: "开始训练" }));
expect(posted.profile).toBe("high_recall");
expect(posted.advanced_config.lr0).toBe(.002);
```

Cover basic payload, all five presets, catalog-derived limits, field/combination errors, dynamic class threshold editor, safety template compatibility, CPU/m/l/x/960/RAM warnings and disabled submit explanations.

- [ ] **Step 2: Run the frontend test and verify failure**

Run: `npm test -- --run src/admin/models/ModelsPage.test.tsx`

Working directory: `frontend`

Expected: FAIL because advanced controls do not exist.

- [ ] **Step 3: Implement typed catalog-driven controls**

```ts
export interface TrainingJobCreate {
  dataset_key: string;
  executor: "local";
  base_model: string;
  imgsz: 640 | 960;
  epochs: number;
  batch: "1" | "2" | "4" | "8" | "auto";
  device: "auto" | "cpu" | "0";
  patience: number;
  mode: "basic" | "advanced";
  profile: TrainingProfile;
  advanced_config: Partial<TrainingAdvancedConfig>;
  acceptance_config: TrainingAcceptanceConfig;
}
```

Split large render blocks into focused local components for profile, optimization, augmentation, runtime, losses and acceptance. Preserve the existing dialog/button visual language and put all disabled reasons in visible status text.

- [ ] **Step 4: Run frontend tests and typecheck**

Run: `npm test -- --run src/admin/models/ModelsPage.test.tsx && npm run typecheck`

Working directory: `frontend`

Expected: PASS.

- [ ] **Step 5: Commit the advanced dialog**

```powershell
git add frontend/src/api/types.ts frontend/src/admin/models/trainingApi.ts frontend/src/admin/models/TrainingJobDialog.tsx frontend/src/admin/models/ModelsPage.tsx frontend/src/admin/admin.css frontend/src/admin/models/ModelsPage.test.tsx
git commit -m "feat: add advanced training dialog"
```

### Task 7: Dynamic Results, Clone and Experiment Comparison UI

**Files:**
- Create: `frontend/src/admin/models/TrainingComparisonDialog.tsx`
- Modify: `frontend/src/admin/models/TrainingJobDetail.tsx`
- Modify: `frontend/src/admin/models/TrainingJobsPanel.tsx`
- Modify: `frontend/src/admin/models/TrainingJobDialog.tsx`
- Modify: `frontend/src/admin/models/trainingApi.ts`
- Modify: `frontend/src/admin/admin.css`
- Modify: `frontend/src/admin/models/ModelsPage.test.tsx`

**Interfaces:**
- Consumes: job detail snapshots, acceptance checks and clone template from Tasks 3–4.
- Produces: `trainingApi.cloneTemplate(id)` and an optional initial request passed to `TrainingJobDialog`.
- Produces: 2–4 job comparison constrained by identical dataset manifest digest.

- [ ] **Step 1: Add failing detail, clone and comparison tests**

```tsx
expect(screen.getByRole("columnheader", { name: "类别" })).toBeInTheDocument();
expect(screen.getByText("fire")).toBeInTheDocument();
fireEvent.click(screen.getByRole("button", { name: "复制为新任务" }));
expect(await screen.findByDisplayValue("high_recall")).toBeInTheDocument();
fireEvent.click(screen.getByLabelText("选择任务 7"));
fireEvent.click(screen.getByLabelText("选择任务 8"));
fireEvent.click(screen.getByRole("button", { name: "对比所选任务" }));
expect(screen.getByRole("dialog", { name: "训练实验对比" })).toBeInTheDocument();
```

Cover acceptance pass/failure reasons, missing metrics, complete config display, confusion matrix and results curve, maximum four selections, mismatched digest rejection, public shared classes and legacy read-only behavior.

- [ ] **Step 2: Run the focused frontend tests**

Run: `npm test -- --run src/admin/models/ModelsPage.test.tsx`

Working directory: `frontend`

Expected: FAIL on missing actions/dialog.

- [ ] **Step 3: Implement dynamic detail, clone and comparison**

Use metric rows from `Object.entries(job.metrics?.classes ?? {})`; remove fixed helmet summary cards from generic views while retaining dynamically named helmet rows. Display the confusion matrix and results curve with descriptive alternative text, and display canonical config as grouped definition lists rather than editable JSON. Permit compare only when 2–4 selected jobs share `dataset_manifest_digest`; otherwise show an accessible inline error.

- [ ] **Step 4: Run frontend suite and production build**

Run: `npm test -- --run && npm run typecheck && npm run build`

Working directory: `frontend`

Expected: PASS.

- [ ] **Step 5: Commit result and comparison UI**

```powershell
git add frontend/src/admin/models/TrainingComparisonDialog.tsx frontend/src/admin/models/TrainingJobDetail.tsx frontend/src/admin/models/TrainingJobsPanel.tsx frontend/src/admin/models/TrainingJobDialog.tsx frontend/src/admin/models/trainingApi.ts frontend/src/admin/admin.css frontend/src/admin/models/ModelsPage.test.tsx
git commit -m "feat: compare and clone training experiments"
```

### Task 8: Documentation and Release Regression Gate

**Files:**
- Modify: `README.md`
- Modify: `docs/training-guide.md`
- Modify: `tests/test_api_compatibility.py`
- Modify: `tests/test_app_startup.py`

**Interfaces:**
- Consumes: all completed user/API behavior.
- Produces: operator instructions for generic datasets, modes, profiles, parameter guidance, acceptance, cloning, comparison, model registration/activation and helmet template semantics.

- [ ] **Step 1: Extend compatibility tests before documentation edits**

```python
assert {"config_schema_version", "training_profile", "advanced_config", "acceptance_config"} <= training_job_columns
assert client.get("/api/training/catalog").json()["config_schema_version"] == 1
```

Assert startup with migrated SQLite, legacy request compatibility and no absolute path in catalog/clone/detail responses.

- [ ] **Step 2: Run backend full suite**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: PASS with only documented optional skips.

- [ ] **Step 3: Update operator and training documentation**

Document exact basic/advanced behavior, five profiles, every exposed parameter group, 640 versus 960 guidance, n/s/m/l/x tradeoffs, generic category examples, acceptance interpretation, task cloning/comparison, continuation semantics, model activation, and explicit three-class helmet labeling. State that generic realtime alert authoring remains a separate feature.

- [ ] **Step 4: Run final local gates**

Run backend: `.venv\Scripts\python.exe -m pytest -q`

Run frontend from `frontend`: `npm test -- --run && npm run typecheck && npm run build`

Run whitespace check: `git diff --check`

Expected: all commands exit 0. Do not launch a long formal training run; use the fake worker and existing real-model inference regression already covered by tests.

- [ ] **Step 5: Commit documentation and compatibility coverage**

```powershell
git add README.md docs/training-guide.md tests/test_api_compatibility.py tests/test_app_startup.py
git commit -m "docs: explain generic advanced training"
```
