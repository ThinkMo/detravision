# Training Dataset Annotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build immutable, versioned server training datasets with a browser-based YOLO bounding-box editor that can discover server-added unlabeled images, require human review, validate them, and publish a reproducible evaluation or training dataset.

**Architecture:** Extend the existing `training_dataset` aggregate with immutable version metadata and a per-image review index. Keep image and YOLO label files as the training source of truth; isolate filesystem containment/copy/scan operations from annotation parsing and publication validation. Expose admin-only same-origin APIs, then add a dataset-version UI and a dependency-free React Canvas annotation workbench.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pillow, PyYAML, React 19, TypeScript, TanStack Query, HTML Canvas, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-training-dataset-annotation-design.md`

## Global Constraints

- Do not migrate the live database, restart the service, or modify `datasets/construction-ppe*` while the current training process is active.
- Existing dataset rows migrate to version `1`, status `published`, and remain usable by their historical training jobs.
- Only `published` and enabled versions may be selected for new training jobs; `editing` versions are never trainable.
- A `published` version is immutable. Every label, class, rescan, or file-writing operation against it returns `409 dataset_published`.
- Browser upload, image deletion, image replacement, and moving images among `train/val/test` are out of scope.
- New images are added by an administrator to an editing version's server directory and discovered only by an explicit rescan.
- An image in `pending_annotation` blocks publication. It becomes publishable only after saving at least one box or explicitly confirming it as `confirmed_empty`.
- All filesystem paths pass resolved-root containment checks; never return absolute paths or raw filesystem/SQL exceptions through APIs or logs.
- Label writes use a random same-directory temporary file, flush/fsync, and `os.replace`.
- Admin authentication and same-origin protection are mandatory for every annotation/version endpoint.
- SQLite is the local single-instance acceptance database. Production remains MySQL `>= 8.0.13`; generate offline MySQL DDL, but do not require a live MySQL service for this plan.
- Use the project `.venv` Python 3.12 and project-owned Node 24 runtime. Keep focused tests fast; defer full integration and deployment until the active training process finishes.

---

## File Structure

Backend responsibilities:

- `backend/db/models.py`: dataset-version and image-review ORM models only.
- `migrations/versions/0008_dataset_annotation.py`: portable schema upgrade/downgrade and existing-row backfill.
- `backend/training/dataset_repository.py`: transactional version and image-index persistence.
- `backend/training/dataset_filesystem.py`: authorized-root clone, scan, path resolution, image metadata, and atomic label files.
- `backend/training/annotation_service.py`: YOLO box contract, per-image read/save/confirm-empty, revision checks, and category mutation.
- `backend/training/dataset_validation.py`: full-version structural checks, semantic warnings, manifest digest, and publication orchestration.
- `backend/training/dataset_service.py`: existing catalog plus version clone/rescan/list composition; it delegates file and annotation work.
- `backend/api/training_routes.py`: stable admin API transport and auditing.
- `backend/schemas.py`: strict request DTOs.
- `backend/main.py`: construct and expose the new services through `app.state`.

Frontend responsibilities:

- `frontend/src/api/types.ts`: version, image, annotation, and validation DTOs.
- `frontend/src/admin/models/trainingApi.ts`: typed endpoint wrappers.
- `frontend/src/admin/models/TrainingDatasetsPanel.tsx`: version cards, clone/rescan/validate/publish actions.
- `frontend/src/admin/models/AnnotationWorkspace.tsx`: workbench orchestration and query/mutation state.
- `frontend/src/admin/models/AnnotationCanvas.tsx`: pointer/keyboard Canvas interactions only.
- `frontend/src/admin/models/AnnotationSidebar.tsx`: image filters, class/box list, review state, and navigation.
- `frontend/src/admin/models/annotationGeometry.ts`: pure coordinate transforms and box manipulation.
- `frontend/src/admin/admin.css`: responsive version/workbench presentation.

---

### Task 1: Persist Dataset Versions and Image Review State

**Files:**
- Create: `migrations/versions/0008_dataset_annotation.py`
- Modify: `backend/db/models.py`
- Modify: `backend/training/dataset_repository.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_training_datasets.py`

**Interfaces:**
- Produces ORM `TrainingDatasetImage` and version fields on `TrainingDataset`.
- Produces repository methods:
  - `create_editing_version(source_id: int, values: Mapping[str, object]) -> dict[str, Any]`
  - `list_versions(root_dataset_id: int) -> list[dict[str, Any]]`
  - `upsert_image_index(dataset_id: int, rows: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]`
  - `list_images(dataset_id: int, split: str, status: str | None, page: int, page_size: int) -> dict[str, Any]`
  - `get_image(dataset_id: int, image_id: int, *, for_update: bool = False) -> dict[str, Any] | None`
  - `update_image_review(image_id: int, expected_revision: int, values: Mapping[str, object]) -> dict[str, Any]`
  - `publish(dataset_id: int, expected_revision: int, digest: str) -> dict[str, Any]`

- [ ] **Step 1: Write migration and repository RED tests**

Add tests that assert existing rows become published v1 rows, version children retain parent/root lineage, and image revision conflicts are rejected:

```python
def test_existing_dataset_migrates_to_published_v1(migrated_database):
    row = migrated_database.execute(sa.text(
        "SELECT version, status, parent_id, root_dataset_id FROM training_dataset"
    )).mappings().one()
    assert row == {
        "version": 1, "status": "published",
        "parent_id": None, "root_dataset_id": None,
    }

def test_image_revision_update_is_compare_and_swap(dataset_repository, editing_dataset):
    image = dataset_repository.upsert_image_index(editing_dataset["id"], [{
        "split": "test", "image_key": "image1.jpg", "display_name": "image1.jpg",
        "width": 640, "height": 480, "review_status": "pending_annotation",
        "content_digest": "a" * 64,
    }])[0]
    changed = dataset_repository.update_image_review(
        image["id"], image["revision"], {"review_status": "annotated"}
    )
    assert changed["revision"] == image["revision"] + 1
    with pytest.raises(TrainingDatasetConflictError):
        dataset_repository.update_image_review(
            image["id"], image["revision"], {"review_status": "confirmed_empty"}
        )
```

- [ ] **Step 2: Run the RED tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_migrations.py tests/test_training_datasets.py -k "published_v1 or editing_version or image_revision or image_index" -q
```

Expected: failures because migration `0008`, version columns, `TrainingDatasetImage`, and repository methods do not exist.

- [ ] **Step 3: Implement migration and ORM models**

Create revision `0008` after `0007`. Add to `training_dataset`:

```python
sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1"))
sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("('published')"))
sa.Column("parent_id", sa.Integer(), sa.ForeignKey("training_dataset.id", ondelete="RESTRICT"))
sa.Column("root_dataset_id", sa.Integer(), sa.ForeignKey("training_dataset.id", ondelete="RESTRICT"))
sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0"))
sa.Column("published_at", sa.DateTime(timezone=True))
sa.Column("manifest_digest", sa.String(length=64))
```

Add checks for `version > 0`, `revision >= 0`, and status in `('editing','published')`; add indexes on `(root_dataset_id, version)` and `status`. Backfill `published_at = created_at` for existing rows.

Create `training_dataset_image` with unique `(dataset_id, split, image_key)`, positive width/height, nonnegative revision, split check, review-status check, digest length 64, and `ON DELETE CASCADE` to the dataset version.

- [ ] **Step 4: Implement repository transactions**

Use a row lock on the source/root dataset when computing the next version on MySQL and `BEGIN IMMEDIATE` on SQLite. Treat a root version as `root_dataset_id is None`; children store `root_dataset_id = source.root_dataset_id or source.id`. Compare-and-swap image updates with one conditional SQL update:

```python
result = session.execute(
    update(TrainingDatasetImage)
    .where(
        TrainingDatasetImage.id == image_id,
        TrainingDatasetImage.revision == expected_revision,
    )
    .values(**values, revision=expected_revision + 1, updated_at=now)
)
if result.rowcount != 1:
    raise TrainingDatasetConflictError("annotation revision conflict")
```

Repository `publish` must require `status == "editing"` and matching dataset revision in the same conditional update.

- [ ] **Step 5: Run GREEN and migration round-trip tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_migrations.py tests/test_training_datasets.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_migrations.py -k "mysql and offline" -q
```

Expected: focused SQLite tests pass; only live-MySQL marked tests may skip; offline DDL includes revision `0008` and both tables.

- [ ] **Step 6: Commit Task 1**

```powershell
git add backend/db/models.py backend/training/dataset_repository.py migrations/versions/0008_dataset_annotation.py tests/test_migrations.py tests/test_training_datasets.py
git commit -m "feat: persist versioned training datasets"
```

---

### Task 2: Clone Versions and Discover Server-Added Images

**Files:**
- Create: `backend/training/dataset_filesystem.py`
- Modify: `backend/training/dataset_service.py`
- Modify: `backend/main.py`
- Create: `tests/test_dataset_filesystem.py`
- Modify: `tests/test_training_dataset_service.py`

**Interfaces:**
- Consumes Task 1 repository version/image methods.
- Produces:
  - `ScannedImage(split: str, image_key: str, display_name: str, width: int, height: int, content_digest: str, has_label: bool)`
  - `DatasetFilesystem.clone(source: Path, destination: Path) -> None`
  - `DatasetFilesystem.scan(dataset: Mapping[str, object], split: str | None = None) -> list[ScannedImage]`
  - `DatasetFilesystem.resolve_image(dataset, split, image_key) -> Path`
  - `DatasetFilesystem.resolve_label(dataset, split, image_key) -> Path`
  - `DatasetService.clone_version(dataset_id: int, created_by: int) -> dict[str, Any]`
  - `DatasetService.rescan(dataset_id: int, split: str | None) -> dict[str, int]`

- [ ] **Step 1: Write clone and scan RED tests**

Cover cache exclusion, temporary-copy cleanup, published immutability, new unlabeled images, existing labels that still require review, decode failures, and external deletion/change detection:

```python
def test_rescan_indexes_unlabeled_test_image_as_pending(service, editing_dataset):
    image = editing_dataset.path / "images/test/new.jpg"
    Image.new("RGB", (320, 240), "white").save(image)
    result = service.rescan(editing_dataset.id, "test")
    assert result["discovered"] == 1
    page = service.list_images(editing_dataset.id, split="test", status="pending_annotation")
    assert page["items"][0]["display_name"] == "new.jpg"
    assert page["items"][0]["review_status"] == "pending_annotation"

def test_clone_failure_removes_temporary_tree(service, source_dataset, monkeypatch):
    monkeypatch.setattr(shutil, "copy2", Mock(side_effect=OSError("disk full")))
    with pytest.raises(DatasetServiceError) as error:
        service.clone_version(source_dataset.id, created_by=1)
    assert error.value.code == "dataset_copy_failed"
    assert not list(source_dataset.root.glob(".dataset-copy-*"))
```

- [ ] **Step 2: Run RED tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dataset_filesystem.py tests/test_training_dataset_service.py -k "clone or rescan or unlabeled or external_change" -q
```

Expected: import/attribute failures for `DatasetFilesystem` and new service methods.

- [ ] **Step 3: Implement safe filesystem operations**

Use only `.jpg`, `.jpeg`, `.png`, `.bmp`, and `.webp`. Resolve every root, split, image, and label path; reject symlinks and paths outside the authorized root. Clone into `.dataset-copy-<random>` under the authorized root, skip files whose lowercase suffix is `.cache`, fsync created files as practical, then rename to an opaque `dataset-<32 hex>` destination.

For scanning, use Pillow `Image.verify()` followed by a fresh open for dimensions, SHA-256 the image bytes with bounded streaming, and map `images/<split>/<relative>` to `labels/<split>/<relative-with-.txt>`. Do not create a label during scan.

- [ ] **Step 4: Implement clone/rescan orchestration**

`clone_version` accepts only a published source, calls the filesystem clone, inserts the editing row and complete image index, and removes the destination if the database transaction fails. `rescan` accepts only editing versions; new images are pending even if a sidecar label exists, while known images with digest or dimension changes become `file_changed` validation errors without silently rewriting labels.

- [ ] **Step 5: Run focused GREEN tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dataset_filesystem.py tests/test_training_dataset_service.py tests/test_training_datasets.py -q
.\.venv\Scripts\python.exe -m compileall -q backend tests
```

Expected: all focused tests pass; Windows symlink tests may conditionally skip only when the OS denies symlink creation.

- [ ] **Step 6: Commit Task 2**

```powershell
git add backend/training/dataset_filesystem.py backend/training/dataset_service.py backend/main.py tests/test_dataset_filesystem.py tests/test_training_dataset_service.py
git commit -m "feat: clone and scan dataset versions"
```

---

### Task 3: Read and Atomically Save YOLO Annotations

**Files:**
- Create: `backend/training/annotation_service.py`
- Modify: `backend/schemas.py`
- Modify: `backend/training/dataset_repository.py`
- Create: `tests/test_annotation_service.py`

**Interfaces:**
- Consumes `DatasetFilesystem.resolve_image/resolve_label` and Task 1 repository compare-and-swap.
- Produces immutable `AnnotationBox(class_id: int, x_center: float, y_center: float, width: float, height: float)`.
- Produces:
  - `AnnotationService.get_image(dataset_id: int, image_id: int) -> dict[str, Any]`
  - `AnnotationService.save(dataset_id: int, image_id: int, revision: int, boxes: Sequence[Mapping[str, object]]) -> dict[str, Any]`
  - `AnnotationService.confirm_empty(dataset_id: int, image_id: int, revision: int) -> dict[str, Any]`
  - `AnnotationService.add_class(dataset_id: int, revision: int, name: str) -> dict[str, Any]`
  - `AnnotationService.rename_class(dataset_id: int, revision: int, class_id: int, name: str) -> dict[str, Any]`
  - `AnnotationService.delete_class(dataset_id: int, revision: int, class_id: int) -> dict[str, Any]`

- [ ] **Step 1: Write annotation contract RED tests**

Test strict types, finite/range checks, published rejection, atomic replacement, revision conflict, confirmed-empty distinction, stable IDs, and used-class deletion:

```python
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.01, 1.01, True, "0.5"])
def test_box_coordinates_are_finite_real_probabilities(service, pending_image, value):
    box = {"class_id": 0, "x_center": value, "y_center": .5, "width": .2, "height": .2}
    with pytest.raises(AnnotationServiceError) as error:
        service.save(pending_image.dataset_id, pending_image.id, 0, [box])
    assert error.value.code == "annotation_invalid"

def test_confirm_empty_creates_empty_label_and_review_state(service, pending_image):
    result = service.confirm_empty(pending_image.dataset_id, pending_image.id, 0)
    assert result["review_status"] == "confirmed_empty"
    assert pending_image.label_path.read_bytes() == b""
```

- [ ] **Step 2: Run RED tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_annotation_service.py -q
```

Expected: collection failure because `backend.training.annotation_service` is absent.

- [ ] **Step 3: Implement strict YOLO parsing and serialization**

Require exact integer `class_id` excluding bool, and exact finite real coordinates in `[0,1]` with width/height greater than zero. Parse existing label rows as exactly five whitespace-separated values. Serialize deterministically with six decimal places and a trailing newline for nonempty labels; confirmed-empty serializes to zero bytes.

- [ ] **Step 4: Implement atomic save with rollback**

Write a same-directory `.label-<random>.tmp`, flush and fsync, preserve the old label bytes, then `os.replace`. Execute the revision update; if it fails, restore the old file atomically or remove the newly created file when none existed. Never turn an empty box list from normal `save` into confirmed-empty; require the dedicated method.

- [ ] **Step 5: Implement stable category mutations**

Append-only creation, rename-in-place, no reordering. Count class IDs by parsing all indexed labels before deletion. Reject deletion with `409 class_in_use` when count is nonzero. All mutations require editing status and dataset revision compare-and-swap.

- [ ] **Step 6: Run GREEN tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_annotation_service.py tests/test_dataset_filesystem.py tests/test_training_dataset_service.py -q
```

Expected: all tests pass with no label temporary files left behind.

- [ ] **Step 7: Commit Task 3**

```powershell
git add backend/training/annotation_service.py backend/schemas.py backend/training/dataset_repository.py tests/test_annotation_service.py
git commit -m "feat: edit YOLO dataset annotations"
```

---

### Task 4: Validate and Publish Immutable Dataset Versions

**Files:**
- Create: `backend/training/dataset_validation.py`
- Modify: `backend/training/dataset_service.py`
- Modify: `backend/training/catalog.py`
- Create: `tests/test_dataset_validation.py`
- Modify: `tests/test_training_catalog.py`
- Modify: `tests/test_training_service.py`

**Interfaces:**
- Consumes filesystem scanning, strict annotation parsing, and repository publication CAS.
- Produces:
  - `ValidationIssue(code: str, severity: Literal["error", "warning"], split: str | None, image_id: int | None, message: str)`
  - `DatasetValidationReport(errors: list[ValidationIssue], warnings: list[ValidationIssue], counts: Mapping[str, object], digest: str | None)`
  - `DatasetValidator.validate(dataset_id: int) -> DatasetValidationReport`
  - `DatasetValidator.publish(dataset_id: int, revision: int, confirm_warnings: bool) -> dict[str, Any]`

- [ ] **Step 1: Write validation/publication RED tests**

Cover pending images, confirmed empty, structural label errors, missing/changed files, person-without-head-label warning, helmet/no-helmet overlap warning, sparse classes, warning confirmation, digest stability, and published immutability:

```python
def test_pending_image_blocks_publication(validator, editing_dataset, pending_image):
    report = validator.validate(editing_dataset.id)
    assert any(issue.code == "annotation_pending" for issue in report.errors)
    with pytest.raises(DatasetValidationError) as error:
        validator.publish(editing_dataset.id, editing_dataset.revision, False)
    assert error.value.code == "dataset_validation_failed"

def test_warning_requires_confirmation_then_publishes(validator, reviewed_dataset):
    first = validator.validate(reviewed_dataset.id)
    assert first.errors == [] and first.warnings
    with pytest.raises(DatasetValidationError) as error:
        validator.publish(reviewed_dataset.id, reviewed_dataset.revision, False)
    assert error.value.code == "dataset_warnings_unconfirmed"
    published = validator.publish(reviewed_dataset.id, reviewed_dataset.revision, True)
    assert published["status"] == "published"
    assert len(published["manifest_digest"]) == 64
```

- [ ] **Step 2: Run RED tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dataset_validation.py tests/test_training_catalog.py -k "version or pending or publish or warning or digest" -q
```

Expected: missing validator and editing versions still appearing trainable.

- [ ] **Step 3: Implement structural validation**

Re-open and verify every indexed image, compare dimensions/digest, parse every label through Task 3, require all image states reviewed, and reject unknown on-disk images until rescan. Compute class/split counts and a deterministic SHA-256 manifest from sorted relative keys, content digests, label digests, class IDs/names, and split names. Do not include absolute paths or timestamps in the digest.

- [ ] **Step 4: Implement semantic warnings**

Use normalized box geometry. Define a person's head region as its upper 40 percent. Warn when no helmet/no-helmet box center falls in that region; warn when helmet and no-helmet boxes overlap at IoU `>= 0.3`; warn when a class has fewer than 20 instances in any nonempty split; warn when a class's split share differs by more than fourfold from the training share. These are diagnostics only and never synthesize labels.

- [ ] **Step 5: Implement atomic publication and catalog filtering**

Revalidate inside the publication boundary, require `confirm_warnings=True` when warnings remain, write a safe relative manifest file atomically, then compare-and-swap status/revision/digest. `DatasetService.resolve` and the training catalog must require `status == "published"`, `enabled is True`, and a valid digest; existing migrated v1 rows remain available through their migration backfill compatibility path until first explicit validation.

- [ ] **Step 6: Run GREEN tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dataset_validation.py tests/test_training_catalog.py tests/test_training_service.py tests/test_training_dataset_service.py -q
```

Expected: all focused tests pass; editing versions never appear in job creation choices.

- [ ] **Step 7: Commit Task 4**

```powershell
git add backend/training/dataset_validation.py backend/training/dataset_service.py backend/training/catalog.py tests/test_dataset_validation.py tests/test_training_catalog.py tests/test_training_service.py
git commit -m "feat: validate and publish dataset versions"
```

---

### Task 5: Expose Secure Dataset Annotation APIs

**Files:**
- Modify: `backend/api/training_routes.py`
- Modify: `backend/schemas.py`
- Modify: `backend/main.py`
- Modify: `tests/test_training_api.py`
- Create: `tests/test_annotation_api.py`

**Interfaces:**
- Consumes Tasks 2–4 services.
- Produces endpoints under `/api/training/datasets/{dataset_id}`:
  - `POST /versions`
  - `POST /rescan`
  - `GET /versions`
  - `GET /images`
  - `GET /images/{image_id}`
  - `GET /images/{image_id}/content`
  - `PUT /images/{image_id}/annotations`
  - `POST /images/{image_id}/confirm-empty`
  - `POST /classes`, `PATCH /classes/{class_id}`, `DELETE /classes/{class_id}`
  - `POST /validate`
  - `POST /publish`

- [ ] **Step 1: Write API RED tests**

Assert admin success, regular-user 403, unauthenticated 401, cross-origin rejection, exact DTO keys, inline image headers, stable errors, no absolute paths, rescan, revision conflict, and published mutation rejection:

```python
def test_unlabeled_image_can_be_annotated_through_api(admin_client, editing_dataset):
    scanned = admin_client.post(f"/api/training/datasets/{editing_dataset.id}/rescan", json={"split": "test"})
    assert scanned.status_code == 200
    image = admin_client.get(
        f"/api/training/datasets/{editing_dataset.id}/images?split=test&status=pending_annotation"
    ).json()["items"][0]
    saved = admin_client.put(
        f"/api/training/datasets/{editing_dataset.id}/images/{image['id']}/annotations",
        json={"revision": image["revision"], "boxes": [{
            "class_id": 2, "x_center": .5, "y_center": .2, "width": .1, "height": .1,
        }]},
    )
    assert saved.status_code == 200
    assert saved.json()["review_status"] == "annotated"
    assert "directory" not in saved.json()
```

- [ ] **Step 2: Run RED tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_annotation_api.py tests/test_training_api.py -k "annotation or version or rescan or publish" -q
```

Expected: new routes return 404.

- [ ] **Step 3: Add strict DTOs and route wiring**

Use Pydantic `extra="forbid"`. Limit boxes per image to 1,000, page size to 100, class name to 100 characters, and split to `Literal["train","val","test"]`. Map domain errors to stable 404/409/422/503 responses. Register services in `create_app` and keep lifespan construction order migration → repositories → services → managers.

- [ ] **Step 4: Add protected image streaming and audit events**

Serve only an indexed image after containment and digest checks. Use detected image MIME, `Content-Disposition: inline`, `X-Content-Type-Options: nosniff`, and no cache for editing versions. Audit clone, rescan, save, confirm-empty, class mutation, validation, and publication with dataset/image IDs and stable result codes only.

- [ ] **Step 5: Run API GREEN and compatibility tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_annotation_api.py tests/test_training_api.py tests/test_authorization.py tests/test_audit.py tests/test_app_startup.py -q
.\.venv\Scripts\python.exe -m compileall -q backend tests
```

Expected: all focused tests pass with no path, SQL, traceback, or submitted-label leakage.

- [ ] **Step 6: Commit Task 5**

```powershell
git add backend/api/training_routes.py backend/schemas.py backend/main.py tests/test_annotation_api.py tests/test_training_api.py
git commit -m "feat: expose dataset annotation API"
```

---

### Task 6: Add Dataset Version Management to the Console

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/admin/models/trainingApi.ts`
- Modify: `frontend/src/admin/models/TrainingDatasetsPanel.tsx`
- Modify: `frontend/src/admin/models/ModelsPage.test.tsx`
- Modify: `frontend/src/admin/admin.css`

**Interfaces:**
- Consumes Task 5 APIs.
- Produces typed `TrainingDatasetVersion`, `TrainingDatasetImagePage`, `DatasetValidationReport`, and version-management UI callbacks.

- [ ] **Step 1: Write frontend version UI RED tests**

Add tests for status/version badges, clone, editing-only rescan, counts, validate/publish confirmation, and immutable published controls:

```tsx
it("creates an editing copy and exposes annotation actions", async () => {
  renderModelsPage({ datasets: [publishedV1] });
  await user.click(screen.getByRole("tab", { name: "训练集" }));
  await user.click(screen.getByRole("button", { name: "创建编辑版本" }));
  expect(api.request).toHaveBeenCalledWith(
    "/api/training/datasets/1/versions", expect.objectContaining({ method: "POST" }),
  );
  expect(await screen.findByText("v2 · 编辑中")).toBeVisible();
  expect(screen.getByRole("button", { name: "继续标注" })).toBeEnabled();
});
```

- [ ] **Step 2: Run RED test**

Use the project Node 24 directory first on `PATH`, then run:

```powershell
npm --prefix frontend run test:run -- ModelsPage.test.tsx
```

Expected: tests fail because version DTOs and controls are absent.

- [ ] **Step 3: Implement typed API and version cards**

Add exact endpoint wrappers, query keys rooted at `['admin','training','datasets']`, and invalidate catalog/version/image queries after mutations. Published cards show “创建编辑版本” and read-only annotation viewing; editing cards show “继续标注”, “重新扫描图片”, “校验” and “发布”. Display pending/annotated/confirmed-empty/error counts and parent/version metadata without server paths.

- [ ] **Step 4: Implement accessible mutation feedback**

Use the shared `ConfirmDialog` for clone and publish-warning confirmation. Keep errors inside the active modal with `role="alert"`; disable only the submitted action, never silently leave an unusable button. Published versions must not render edit controls.

- [ ] **Step 5: Run GREEN, typecheck, and build**

```powershell
npm --prefix frontend run test:run -- ModelsPage.test.tsx
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: focused tests, TypeScript, and Vite production build pass under Node 24.

- [ ] **Step 6: Commit Task 6**

```powershell
git add frontend/src/api/types.ts frontend/src/admin/models/trainingApi.ts frontend/src/admin/models/TrainingDatasetsPanel.tsx frontend/src/admin/models/ModelsPage.test.tsx frontend/src/admin/admin.css
git commit -m "feat: manage dataset versions in console"
```

---

### Task 7: Build the Canvas Annotation Workbench

**Files:**
- Create: `frontend/src/admin/models/annotationGeometry.ts`
- Create: `frontend/src/admin/models/AnnotationCanvas.tsx`
- Create: `frontend/src/admin/models/AnnotationSidebar.tsx`
- Create: `frontend/src/admin/models/AnnotationWorkspace.tsx`
- Create: `frontend/src/admin/models/AnnotationWorkspace.test.tsx`
- Modify: `frontend/src/admin/models/ModelsPage.tsx`
- Modify: `frontend/src/admin/admin.css`

**Interfaces:**
- Consumes Task 6 DTO/API wrappers.
- Produces pure geometry functions:
  - `screenToImage(point, viewport) -> Point`
  - `imageToNormalized(box, imageSize) -> AnnotationBoxInput`
  - `normalizedToImage(box, imageSize) -> PixelBox`
  - `clampBox(box, imageSize, minimumPixels) -> PixelBox | null`
  - `hitTest(point, boxes, handleRadius) -> HitResult | null`
- Produces route/view state for dataset ID, split, status filter, page, and selected image.

- [ ] **Step 1: Write pure geometry RED tests**

Test drawing in every drag direction, zoom/pan transforms, eight handles, boundary clamping, and minimum-size rejection:

```ts
it("maps a zoomed pointer drag back to normalized image coordinates", () => {
  const viewport = { scale: 2, offsetX: 40, offsetY: 20 };
  const start = screenToImage({ x: 240, y: 120 }, viewport);
  const end = screenToImage({ x: 440, y: 320 }, viewport);
  expect(imageToNormalized(boxFromPoints(start, end), { width: 1000, height: 500 }))
    .toEqual({ x_center: .15, y_center: .2, width: .1, height: .2 });
});
```

- [ ] **Step 2: Run geometry RED tests**

```powershell
npm --prefix frontend run test:run -- AnnotationWorkspace.test.tsx
```

Expected: module resolution failure for annotation geometry/workspace files.

- [ ] **Step 3: Implement geometry and Canvas interactions**

Keep all authoritative boxes in original-image pixel coordinates. Canvas render converts pixels to viewport coordinates. Pointer state is one of idle/drawing/moving/resizing/panning; pointer capture ends operations outside the element. Enforce image bounds and a four-original-pixel minimum. Use wheel zoom centered at the pointer, clamp scale from `0.25` to `8`, and expose visible focus plus an accessible textual box list.

- [ ] **Step 4: Implement workbench queries, navigation, and save state**

Fetch image pages by dataset/split/status. Fetch selected annotation detail and render its protected URL. Debounce saves by 600 ms; before next/previous navigation, flush the save mutation. Store unsaved recovery under a key containing dataset ID, image ID, and server revision; restore only when revisions match. On `annotation_conflict`, preserve local boxes, show a blocking alert, and offer reload rather than retrying blindly.

- [ ] **Step 5: Implement categories and review actions**

Render classes dynamically and allow class locking. Normal save requires at least one box and marks `annotated`; a separate “确认无目标” action requires confirmation and calls the dedicated endpoint. Add/delete/rename class controls honor server conflicts. Keyboard controls are `Ctrl+S`, `Delete`, `Escape`, undo/redo, and previous/next arrows when focus is not in a text field.

- [ ] **Step 6: Expand behavior tests to GREEN**

Test mouse draw/move/resize, class lock, save-and-next, pending blocking, confirm-empty, autosave failure, revision conflict, local recovery, keyboard controls, status filtering, and published read-only mode.

Run:

```powershell
npm --prefix frontend run test:run -- AnnotationWorkspace.test.tsx ModelsPage.test.tsx
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: all focused tests and build pass with no React warnings.

- [ ] **Step 7: Commit Task 7**

```powershell
git add frontend/src/admin/models/annotationGeometry.ts frontend/src/admin/models/AnnotationCanvas.tsx frontend/src/admin/models/AnnotationSidebar.tsx frontend/src/admin/models/AnnotationWorkspace.tsx frontend/src/admin/models/AnnotationWorkspace.test.tsx frontend/src/admin/models/ModelsPage.tsx frontend/src/admin/admin.css
git commit -m "feat: annotate training images in console"
```

---

### Task 8: Documentation and Deferred Deployment Acceptance

**Files:**
- Modify: `README.md`
- Modify: `docs/training-guide.md`
- Modify: `.env.example`
- Create: `tests/test_dataset_annotation_flow.py`

**Interfaces:**
- Consumes the complete Tasks 1–7 workflow.
- Produces operator instructions and one isolated SQLite acceptance test.

- [ ] **Step 1: Write the isolated flow RED test**

Build a temporary authorized root with a published v1 dataset. Through the real ASGI app: clone v2, add two server-side `images/test` files, rescan, annotate one, confirm the other empty, assert publication is initially blocked while pending, publish after review, and create a training request that selects v2 while v2 files remain unchanged.

```python
def test_admin_builds_evaluation_version_from_unlabeled_images(app_factory, tmp_path):
    app = app_factory(dataset_root=tmp_path / "datasets")
    with TestClient(app) as client:
        clone = client.post("/api/training/datasets/1/versions").json()
        dataset = app.state.training_dataset_repository.get(clone["id"])
        test_dir = tmp_path / "datasets" / dataset["directory"] / "images/test"
        Image.new("RGB", (80, 60), "white").save(test_dir / "target.jpg")
        Image.new("RGB", (80, 60), "black").save(test_dir / "negative.jpg")
        client.post(f"/api/training/datasets/{clone['id']}/rescan", json={"split": "test"})
        images = client.get(
            f"/api/training/datasets/{clone['id']}/images?split=test&status=pending_annotation"
        ).json()["items"]
        assert client.post(
            f"/api/training/datasets/{clone['id']}/publish",
            json={"revision": clone["revision"], "confirm_warnings": False},
        ).status_code == 409
        by_name = {item["display_name"]: item for item in images}
        target = by_name["target.jpg"]
        client.put(
            f"/api/training/datasets/{clone['id']}/images/{target['id']}/annotations",
            json={"revision": target["revision"], "boxes": [{
                "class_id": 2, "x_center": .5, "y_center": .25,
                "width": .2, "height": .2,
            }]},
        ).raise_for_status()
        negative = by_name["negative.jpg"]
        client.post(
            f"/api/training/datasets/{clone['id']}/images/{negative['id']}/confirm-empty",
            json={"revision": negative["revision"]},
        ).raise_for_status()
        checked = client.post(f"/api/training/datasets/{clone['id']}/validate").json()
        assert checked["errors"] == []
        current = app.state.training_dataset_repository.get(clone["id"])
        published = client.post(
            f"/api/training/datasets/{clone['id']}/publish",
            json={"revision": current["revision"], "confirm_warnings": True},
        ).json()
        assert published["status"] == "published"
        catalog = client.get("/api/training/catalog").json()
        assert any(item["key"] == f"dataset:{clone['id']}" for item in catalog["datasets"])
```

- [ ] **Step 2: Run the isolated flow test before documentation**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_dataset_annotation_flow.py -q
```

Expected: test passes only when the end-to-end backend workflow is complete; fix contract seams in their owning modules before proceeding.

- [ ] **Step 3: Document the operator workflow**

Document exact steps:

1. wait for active training to finish;
2. back up database and datasets;
3. create an editing copy from a published version;
4. copy new images into the editing version's `images/test` server directory;
5. click “重新扫描图片”;
6. annotate every pending image or explicitly confirm no target;
7. resolve structural errors, review warnings, and publish;
8. select the new published version for evaluation/training.

State that direct modification of a published directory is unsupported, browser upload/delete/move is unavailable, and additional classes do not automatically create alarm rules.

- [ ] **Step 4: Run final focused verification without touching the live service**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_migrations.py tests/test_training_datasets.py tests/test_dataset_filesystem.py tests/test_training_dataset_service.py tests/test_annotation_service.py tests/test_dataset_validation.py tests/test_annotation_api.py tests/test_training_api.py tests/test_dataset_annotation_flow.py -q
npm --prefix frontend run test:run -- ModelsPage.test.tsx AnnotationWorkspace.test.tsx
npm --prefix frontend run typecheck
npm --prefix frontend run build
.\.venv\Scripts\python.exe -m compileall -q backend training tests
git diff --check
```

Expected: all focused SQLite/backend/frontend tests pass; only explicitly gated live-MySQL or Windows-symlink tests may skip. Do not run migration against `data/safety_detect.db` and do not restart PID 11184 while training remains active.

- [ ] **Step 5: Commit Task 8**

```powershell
git add README.md docs/training-guide.md .env.example tests/test_dataset_annotation_flow.py
git commit -m "docs: explain dataset annotation workflow"
```

- [ ] **Step 6: Deploy only after explicit confirmation that training ended**

After the user confirms training completion: stop the service, back up `data/safety_detect.db` and the dataset roots, run `alembic upgrade head`, rebuild frontend, start the service, and perform the smoke flow against a disposable copied dataset version. Report MySQL as not run unless `TEST_MYSQL_URL` is explicitly configured and authorized.

---

## Plan Self-Review Result

- Spec coverage: version immutability, clone cleanup, server-added unlabeled scan, pending/confirmed-empty distinction, all three splits, dynamic classes, Canvas draw/edit, autosave/conflict recovery, validation warnings/errors, publication digest, training selection, auth/path safety, audit, documentation, and deferred deployment each map to a task.
- Placeholder scan: no `TBD`, `TODO`, “implement later”, or unspecified error-handling steps remain.
- Type consistency: repository/service/API/frontend names use `editing|published`, `pending_annotation|annotated|confirmed_empty`, dataset/image `revision`, and the same clone/rescan/validate/publish boundaries throughout.
