# Batch Image Inference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add persistent, authenticated multi-image inference tasks with progress, annotated outputs, history, retention cleanup, and frontend result/download workflows without destabilizing real-time RTSP inference.

**Architecture:** A thread-safe DetectorProvider owns the active Ultralytics model and returns the model version with every prediction. A bounded single batch worker claims queued images from the database, yields the shared detector between images, stores annotated output through FileStorage, and maintains resumable task counters. FastAPI validates multipart uploads and enforces task ownership; the React page polls active tasks and presents per-image results.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, OpenCV, Pillow, Ultralytics, React 19, TypeScript, TanStack Query, Vitest, pytest

## Global Constraints

- This plan starts only after both 2026-08-15-platform-auth-foundation.md and 2026-08-15-management-console.md are complete.
- A request accepts 1 to 20 PNG, JPEG, or WebP images.
- Each uploaded file is at most 10 MB and each decoded image is at most 40,000,000 pixels.
- Tasks and files are retained for 30 days by default.
- Task states are queued, processing, completed, partial_failed, and failed.
- A failed image does not block the remaining images.
- Ordinary users may see and download only their own tasks; administrators may access all tasks.
- New tasks are rejected when no real detector is loaded.
- Every image records the model version actually used.
- Whole-task ZIP files are generated on demand and deleted after the response.
- Never trust client filenames as storage paths.

---

### Task 1: Thread-Safe Detector Provider and Real-Time Integration

**Files:**
- Create: backend/engine/provider.py
- Modify: backend/engine/detector.py
- Modify: backend/engine/__init__.py
- Modify: backend/pipeline/runtime.py
- Modify: backend/main.py
- Create: tests/test_detector_provider.py
- Modify: tests/test_rules.py

**Interfaces:**
- Produces: PredictionResult(detections: list[Detection], model_version_id: int | None)
- Produces: DetectorProvider(detector: Detector, model_version_id: int | None)
- Produces: DetectorProvider.predict_realtime(image: Any) -> PredictionResult
- Produces: DetectorProvider.predict_batch(image: Any) -> PredictionResult
- Produces: DetectorProvider.replace(detector: Detector, model_version_id: int | None) -> None
- Produces: DetectorProvider.available -> bool

- [ ] **Step 1: Write concurrency and version tests**

~~~python
def test_replace_is_atomic_and_result_records_version():
    provider = DetectorProvider(FakeDetector("helmet"), model_version_id=3)
    first = provider.predict_batch(image())
    provider.replace(FakeDetector("no_helmet"), model_version_id=4)
    second = provider.predict_batch(image())
    assert first.model_version_id == 3
    assert second.model_version_id == 4


def test_waiting_realtime_prediction_runs_before_next_batch_prediction():
    detector = BlockingDetector()
    provider = DetectorProvider(detector, model_version_id=1)
    order = run_contending_predictions(provider)
    assert order == ["batch-1", "realtime", "batch-2"]
~~~

- [ ] **Step 2: Run tests and verify failure**

Run: .venv\Scripts\python.exe -m pytest tests/test_detector_provider.py -v

Expected: FAIL because DetectorProvider does not exist.

- [ ] **Step 3: Implement prediction result and provider locking**

~~~python
@dataclass(frozen=True, slots=True)
class PredictionResult:
    detections: list[Detection]
    model_version_id: int | None

class DetectorProvider:
    def __init__(self, detector: Detector, model_version_id: int | None):
        self._condition = threading.Condition()
        self._detector = detector
        self._model_version_id = model_version_id
        self._active = False
        self._realtime_waiters = 0

    def predict_realtime(self, image: Any) -> PredictionResult:
        with self._condition:
            self._realtime_waiters += 1
            try:
                self._condition.wait_for(lambda: not self._active)
                self._active = True
                detector, version = self._detector, self._model_version_id
            finally:
                self._realtime_waiters -= 1
        return self._finish_prediction(detector, version, image)

    def predict_batch(self, image: Any) -> PredictionResult:
        with self._condition:
            self._condition.wait_for(lambda: not self._active and self._realtime_waiters == 0)
            self._active = True
            detector, version = self._detector, self._model_version_id
        return self._finish_prediction(detector, version, image)

    def replace(self, detector: Detector, model_version_id: int | None) -> None:
        with self._condition:
            self._condition.wait_for(lambda: not self._active)
            self._detector = detector
            self._model_version_id = model_version_id

    @property
    def available(self) -> bool:
        with self._condition:
            return not isinstance(self._detector, NoopDetector)

    def _finish_prediction(
        self, detector: Detector, version: int | None, image: Any
    ) -> PredictionResult:
        try:
            return PredictionResult(detector.predict(image), version)
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()
~~~

Use a Condition, one active prediction slot, and a realtime_waiters count. Batch acquisition waits while any real-time caller is queued. Capture detector and version under the same lock and hold the prediction slot until the call returns. NoopDetector sets available=false.

- [ ] **Step 4: Route RuntimeManager through the provider**

RuntimeManager receives DetectorProvider instead of a mutable Detector. Frame processing calls predict_realtime. Model reconciliation constructs the replacement outside the provider lock, then calls replace with the database model ID. The initial MODEL_PATH detector uses model_version_id=None.

- [ ] **Step 5: Run provider and runtime regressions**

Run: .venv\Scripts\python.exe -m pytest tests/test_detector_provider.py tests/test_rules.py tests/test_frame_queue.py -v

Expected: PASS.

- [ ] **Step 6: Commit**

~~~powershell
git add backend/engine backend/pipeline/runtime.py backend/main.py tests/test_detector_provider.py tests/test_rules.py
git commit -m "refactor: coordinate realtime and batch inference"
~~~

---

### Task 2: Batch Schema, Migration, and Repository

**Files:**
- Modify: backend/db/models.py
- Modify: backend/db/repositories.py
- Create: migrations/versions/0003_batch_jobs.py
- Create: backend/batch/repository.py
- Create: backend/batch/types.py
- Create: backend/batch/__init__.py
- Create: tests/test_batch_repository.py
- Modify: tests/test_migrations.py

**Interfaces:**
- Produces ORM classes BatchJob and BatchImage
- Produces: BatchRepository.create_job(user_id: int, images: list[PendingImage]) -> BatchJobView
- Produces: BatchRepository.claim_next_image() -> ClaimedImage | None
- Produces: BatchRepository.complete_image(image_id: int, result: CompletedImage) -> None
- Produces: BatchRepository.fail_image(image_id: int, error_code: str, message: str) -> None
- Produces: BatchRepository.recover_interrupted() -> int
- Produces: BatchRepository.list_expired(now: datetime) -> list[ExpiredJob]

- [ ] **Step 1: Write migration and state tests**

~~~python
def test_batch_migration_has_foreign_keys(sqlite_url):
    run_migrations(sqlite_url)
    inspector = inspect(create_engine(sqlite_url))
    assert {fk["referred_table"] for fk in inspector.get_foreign_keys("batch_job")} == {"user"}
    assert {fk["referred_table"] for fk in inspector.get_foreign_keys("batch_image")} == {
        "batch_job", "model_version"
    }


def test_partial_failure_recomputes_job(repository, seeded_user):
    job = repository.create_job(seeded_user.id, [pending("a.jpg"), pending("b.jpg")])
    first, second = repository.list_images(job.id)
    repository.complete_image(first.id, completed())
    repository.fail_image(second.id, "decode_failed", "图片无法解码")
    assert repository.get_job(job.id).status == "partial_failed"
~~~

- [ ] **Step 2: Run tests and verify failure**

Run: .venv\Scripts\python.exe -m pytest tests/test_batch_repository.py tests/test_migrations.py -v

Expected: FAIL because batch tables and repository do not exist.

- [ ] **Step 3: Define cross-database batch tables**

batch_job columns: id, user_id, status, image_total, success_count, failure_count, expires_at, created_at, updated_at. batch_image columns: id, job_id, display_name, source_key, annotated_key, status, error_code, error_message, model_version_id nullable, detections_json, created_at, updated_at.

Use string checks through application validation rather than dialect-specific enum types. Add indexes on job user/created time, job status, image job/status, and expires_at.

- [ ] **Step 4: Implement atomic claiming and state recomputation**

With one worker, claim by selecting the oldest queued image and conditionally updating where status=queued. If the update count is zero, retry once. Every complete/fail call updates the image and recomputes its parent counts and terminal status in the same transaction.

recover_interrupted changes processing images back to queued, then recomputes all nonterminal jobs. It is idempotent.

- [ ] **Step 5: Run repository tests on SQLite and MySQL**

Run: .venv\Scripts\python.exe -m pytest tests/test_batch_repository.py tests/test_migrations.py -v

Run with TEST_MYSQL_URL configured: .venv\Scripts\python.exe -m pytest tests/test_batch_repository.py -m mysql -v

Expected: PASS on both database engines.

- [ ] **Step 6: Commit**

~~~powershell
git add backend/db backend/batch migrations/versions/0003_batch_jobs.py tests/test_batch_repository.py tests/test_migrations.py
git commit -m "feat: persist batch inference jobs"
~~~

---

### Task 3: Batch Worker, Annotation, Recovery, and Retention

**Files:**
- Create: backend/batch/annotate.py
- Create: backend/batch/worker.py
- Create: backend/batch/cleanup.py
- Modify: backend/batch/__init__.py
- Modify: backend/main.py
- Create: tests/test_batch_worker.py
- Create: tests/test_batch_cleanup.py

**Interfaces:**
- Consumes: BatchRepository, DetectorProvider, FileStorage
- Produces: annotate(image: np.ndarray, detections: list[Detection]) -> np.ndarray
- Produces: BatchWorker.start() -> None and BatchWorker.stop() -> None
- Produces: RetentionCleaner.run_once(now: datetime) -> CleanupSummary

- [ ] **Step 1: Write worker tests**

~~~python
def test_worker_continues_after_one_image_fails(batch_context):
    job = batch_context.create_two_image_job(second_is_corrupt=True)
    batch_context.worker.process_available()
    result = batch_context.repository.get_job(job.id)
    assert result.status == "partial_failed"
    assert result.success_count == 1
    assert result.failure_count == 1


def test_worker_records_actual_model_version(batch_context):
    job = batch_context.create_job()
    batch_context.provider.replace(FakeDetector(), model_version_id=8)
    batch_context.worker.process_available()
    assert batch_context.repository.list_images(job.id)[0].model_version_id == 8
~~~

- [ ] **Step 2: Write cleanup tests**

~~~python
def test_cleanup_keeps_record_when_any_file_delete_fails(cleaner, expired_job, storage):
    storage.fail_delete(expired_job.images[0].source_key)
    summary = cleaner.run_once(now=expired_job.expires_at)
    assert summary.deleted_jobs == 0
    assert cleaner.repository.get_job(expired_job.id) is not None
~~~

- [ ] **Step 3: Run tests and verify failure**

Run: .venv\Scripts\python.exe -m pytest tests/test_batch_worker.py tests/test_batch_cleanup.py -v

Expected: FAIL because worker, annotation, and cleanup modules do not exist.

- [ ] **Step 4: Implement deterministic annotation**

Decode through OpenCV after upload validation. Draw clamped rectangles and a filled label background containing label plus confidence rounded to two decimals. Choose colors from a stable hash of the label so the same class remains visually consistent. Encode annotated output as JPEG quality 90.

- [ ] **Step 5: Implement the bounded worker**

On start call recover_interrupted, then launch one daemon thread. The loop claims one image, reads its source key, decodes it, calls provider.predict_batch, annotates, saves the output, and commits completion. Map decode, inference, storage, and unexpected errors to stable codes. Delete a newly written output if database completion fails. Sleep through an Event wait so stop interrupts promptly.

- [ ] **Step 6: Implement retention cleanup**

Run once during startup and every six hours in a daemon thread. For each expired job, delete all source and annotated keys; delete database rows only when every existing file deletion succeeds. Missing files count as already deleted. Log storage keys and error codes, not absolute paths.

- [ ] **Step 7: Run worker and cleanup tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_batch_worker.py tests/test_batch_cleanup.py -v

Expected: PASS.

- [ ] **Step 8: Commit**

~~~powershell
git add backend/batch backend/main.py tests/test_batch_worker.py tests/test_batch_cleanup.py
git commit -m "feat: process and retain batch image results"
~~~

---

### Task 4: Authenticated Batch Upload and Download API

**Files:**
- Create: backend/api/batch_routes.py
- Create: backend/batch/service.py
- Modify: backend/schemas.py
- Modify: backend/main.py
- Create: tests/test_batch_api.py

**Interfaces:**
- Produces: POST /api/batch-jobs
- Produces: GET /api/batch-jobs
- Produces: GET /api/batch-jobs/{id}
- Produces: GET /api/batch-jobs/{id}/images/{image_id}/download
- Produces: GET /api/batch-jobs/{id}/download

- [ ] **Step 1: Write upload validation and ownership tests**

~~~python
def test_upload_rejects_more_than_twenty_images(user_client, png_bytes):
    files = [("files", ("image-" + str(index) + ".png", png_bytes, "image/png")) for index in range(21)]
    response = user_client.post("/api/batch-jobs", files=files)
    assert response.status_code == 422
    assert response.json()["code"] == "too_many_images"


def test_user_cannot_read_another_users_job(user_client, other_users_job):
    assert user_client.get("/api/batch-jobs/" + str(other_users_job.id)).status_code == 404


def test_upload_rejected_without_loaded_model(user_client, png_bytes, noop_provider):
    response = user_client.post(
        "/api/batch-jobs",
        files={"files": ("one.png", png_bytes, "image/png")},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "model_unavailable"
~~~

Cover zero files, 10 MB boundary, corrupt content, fake MIME, pixel limit, transactional cleanup, admin visibility, source download denial, single annotated download, ZIP manifest, and temporary ZIP deletion.

- [ ] **Step 2: Run tests and verify failure**

Run: .venv\Scripts\python.exe -m pytest tests/test_batch_api.py -v

Expected: FAIL because batch routes do not exist.

- [ ] **Step 3: Implement transactional upload creation**

Validate provider.available before reading files. Read each UploadFile through a capped stream, validate with storage.images, and save under batch-source/YYYY/MM/DD. If any file fails, delete every key saved by the request and do not create a job. On success create one job and its image records in one transaction with expires_at equal to created_at plus 30 days.

- [ ] **Step 4: Implement visibility and pagination**

GET list accepts page, page_size at most 100, status, created_from, and created_to. Ordinary users receive only their user_id; administrators may optionally filter by user_id. Detail returns job counters and ordered image summaries, including a result URL but never a storage key or absolute path.

- [ ] **Step 5: Implement safe downloads**

Single-image download verifies task visibility and successful annotated_key. ZIP generation writes to a temporary file containing results.json plus successful annotated images named with server-generated ordinal prefixes and sanitized display names. Attach a FastAPI BackgroundTask that deletes the temporary ZIP after response completion.

- [ ] **Step 6: Run API tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_batch_api.py -v

Expected: PASS.

- [ ] **Step 7: Commit**

~~~powershell
git add backend/api/batch_routes.py backend/batch/service.py backend/schemas.py backend/main.py tests/test_batch_api.py
git commit -m "feat: expose batch image inference API"
~~~

---

### Task 5: Multi-Image Recognition Frontend

**Files:**
- Create: frontend/src/batch/BatchPage.tsx
- Create: frontend/src/batch/UploadDropzone.tsx
- Create: frontend/src/batch/JobList.tsx
- Create: frontend/src/batch/JobDetail.tsx
- Create: frontend/src/batch/ResultCard.tsx
- Create: frontend/src/batch/batchApi.ts
- Create: frontend/src/batch/batch.css
- Modify: frontend/src/api/types.ts
- Modify: frontend/src/app/App.tsx
- Create: frontend/src/batch/BatchPage.test.tsx

**Interfaces:**
- Consumes batch API from Task 4
- Produces: useBatchJobs filters and list query
- Produces: useBatchJob(jobId) polling every 1000 ms while queued or processing
- Produces routes /batch and /batch/{jobId}

- [ ] **Step 1: Write upload and progress tests**

~~~tsx
it("rejects a twenty-first file before upload", async () => {
  renderBatchPage();
  await chooseFiles(makeImages(21));
  expect(screen.getByText("单次最多上传 20 张图片")).toBeInTheDocument();
  expect(api.createBatchJob).not.toHaveBeenCalled();
});


it("polls until a partially failed task reaches a terminal state", async () => {
  api.jobResponses([processingJob(), partialFailedJob()]);
  renderBatchJob(12);
  expect(await screen.findByText("处理中 1 / 2")).toBeInTheDocument();
  expect(await screen.findByText("部分失败")).toBeInTheDocument();
  expect(screen.getByText("图片无法解码")).toBeInTheDocument();
});
~~~

- [ ] **Step 2: Run tests and verify failure**

Run: npm run test:run -- src/batch

Working directory: frontend

Expected: FAIL because batch components do not exist.

- [ ] **Step 3: Implement upload selection**

The dropzone supports click, drag/drop, keyboard activation, and multiple selection. Validate count, each file size, browser MIME, and duplicate name/size/lastModified triples before submission. Render a removable preview list, total size, and upload progress. Revoke every preview object URL after removal or unmount.

- [ ] **Step 4: Implement task history and polling**

List task date, creator for administrators, counts, status, and expiry. Poll only queued/processing detail queries every second and stop on terminal status or unmount. Preserve list filters in the URL.

- [ ] **Step 5: Implement result cards and downloads**

Display the annotated image, original display name, actual model version, detection count, each label/confidence, and failure message. Provide single-result and whole-task download buttons using normal authenticated same-origin navigation. Use responsive cards on narrow screens.

- [ ] **Step 6: Run tests and build**

Run: npm run test:run -- src/batch

Run: npm run typecheck

Run: npm run build

Working directory: frontend

Expected: PASS.

- [ ] **Step 7: Commit**

~~~powershell
git add frontend/src/batch frontend/src/api/types.ts frontend/src/app/App.tsx
git commit -m "feat: add multi-image recognition workspace"
~~~

---

### Task 6: Full Verification with the Trained Model and Operator Documentation

**Files:**
- Modify: README.md
- Modify: .env.example
- Create: tests/test_batch_end_to_end.py
- Modify: backend/core/config.py

**Interfaces:**
- Consumes every batch backend and frontend component
- Produces documented BATCH_RETENTION_DAYS with default 30 and BATCH_WORKER_COUNT fixed to 1 for this release

- [ ] **Step 1: Add a real-model smoke test**

~~~python
@pytest.mark.model
def test_real_model_batch_smoke(real_model_app, helmet_test_images):
    client = login_changed_admin(real_model_app)
    response = client.post("/api/batch-jobs", files=upload_files(helmet_test_images[:2]))
    job = wait_for_terminal_job(client, response.json()["id"], timeout=60)
    assert job["status"] in {"completed", "partial_failed"}
    assert any(image["status"] == "completed" for image in job["images"])
    assert all(image.get("annotated_url") for image in job["images"] if image["status"] == "completed")
~~~

- [ ] **Step 2: Run focused and full backend tests**

Run: .venv\Scripts\python.exe -m pytest tests/test_batch_end_to_end.py -m model -v

Run: .venv\Scripts\python.exe -m pytest -v

Expected: both commands PASS using runs/detect/train-3/weights/best.pt for the marked smoke test.

- [ ] **Step 3: Run complete frontend verification**

Run: npm run typecheck

Run: npm run test:run

Run: npm run build

Working directory: frontend

Expected: PASS.

- [ ] **Step 4: Perform the acceptance flow**

Start the same-origin service, log in as the bootstrap administrator, change the initial password, upload two known helmet test images, wait for completion, inspect annotated results, download one image, download the ZIP, and confirm real-time monitoring reconnects after the batch finishes.

- [ ] **Step 5: Document operations**

README must explain upload limits, statuses, model-unavailable behavior, ownership, download contents, 30-day retention, cleanup retry, storage directory, actual-model smoke command, and the effect of batch inference on a single GPU.

- [ ] **Step 6: Commit**

~~~powershell
git add README.md .env.example backend/core/config.py tests/test_batch_end_to_end.py
git commit -m "docs: complete batch inference operations"
~~~
