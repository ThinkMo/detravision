# Helmet-Focused Inference UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复多图上传重复打开文件窗口的问题，可靠支持一次和分次多选，并让批量与实时识别直接、优先展示未佩戴安全帽的人员。

**Architecture:** 后端在现有受保护下载链路旁新增同源、鉴权的 inline 预览端点，DTO 同时返回严格可验证的预览和下载 URL。前端拆开文件选择触发器与提交按钮，结果卡使用预览 URL 内嵌图片并从完整 detections 派生 `no_helmet` 违规统计；批量与实时画面共用确定性的类别标注样式，但不改变事件规则、模型或持久化结构。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、Pillow/OpenCV、React 19、TypeScript、TanStack Query、Vitest、Testing Library、pytest。

## Global Constraints

- 识别主目标是 `no_helmet`；`person`、`helmet` 和其他检测仍完整保留，只降低视觉权重。
- 单任务最多 20 张图片，每张最多 10 MiB；支持 `.png`、`.jpg`、`.jpeg`、`.webp`，扩展名、声明 MIME 和真实解码格式必须一致。
- 预览与下载都必须使用现有 Cookie 会话、owner/admin 权限、job/image 归属和本地存储路径边界，不得返回或记录本地路径、RTSP、存储 key、密码或会话秘密。
- `/preview` 必须使用 `Content-Disposition: inline` 和 `X-Content-Type-Options: nosniff`；现有 `/download` 与 ZIP 必须继续使用附件下载语义。
- 上传区一次用户操作只能触发一次系统文件选择器；文件 input 必须保留 `multiple`，提交按钮绝不能触发选择器。
- 上传失败必须保留已选图片；成功后进入任务详情；处理中每秒轮询，终态停止。
- 实时事件关联、连续帧、冷却和告警规则不变。
- 本轮只验收本地 SQLite 单实例；生产 MySQL 与部署验收不在范围内。
- 前端命令必须使用 Node.js 24.x；更新前端源码后必须重新生成 `frontend/dist`。

---

## File Structure

- `backend/api/batch_routes.py`：批量任务 DTO URL 生成，以及 preview/download HTTP 语义。
- `backend/batch/service.py`：复用 owner/admin、完成状态、图像归属、存储边界和安全错误映射，不新增第二套文件查找规则。
- `backend/schemas.py`：为 `BatchImageResponse` 增加 `preview_url`。
- `tests/test_batch_api.py`：预览鉴权、安全头、路径/错误隔离及下载不回退的 API 契约。
- `frontend/src/api/types.ts`：同步 `BatchImageResult.preview_url`。
- `frontend/src/batch/batchApi.ts`：分别校验 preview 和 download 的站内绝对路径。
- `frontend/src/batch/UploadDropzone.tsx`：单一选择触发器、多选追加、提交隔离与失败保留。
- `frontend/src/batch/ResultCard.tsx`：内嵌预览、违规优先统计、其他检测弱化与次要下载操作。
- `frontend/src/batch/BatchPage.test.tsx`：上传交互、URL 白名单和结果呈现行为测试。
- `frontend/src/styles/global.css`：上传触发器、违规摘要、检测层级和预览失败态样式。
- `backend/batch/annotate.py`：确定性的类别配色与线宽；`no_helmet` 使用高对比红色粗框。
- `backend/pipeline/runtime.py`：把实时 detections 传给共享标注器后再编码广播，不改变规则计算。
- `tests/test_batch_worker.py`：像素级/区域级验证类别样式与边界安全。
- `tests/test_runtime_security.py`：验证实时广播使用标注帧且事件规则调用保持原检测集合。
- `README.md`：更新多选、直接预览、下载语义和安全帽违规优先说明。

---

### Task 1: Secure Inline Batch Result Preview

**Files:**
- Modify: `backend/schemas.py:118-131`
- Modify: `backend/api/batch_routes.py:155-175,268-285`
- Modify: `backend/batch/service.py:226-255`
- Test: `tests/test_batch_api.py:552-640`

**Interfaces:**
- Consumes: `BatchService.open_result(user: AuthenticatedUser, job_id: int, image_id: int) -> ResultDownload` and `_stream_and_close(stream) -> Iterator[bytes]`.
- Produces: `BatchImageResponse.preview_url: str`, `GET /api/batch-jobs/{job_id}/images/{image_id}/preview`, and unchanged `result_url` attachment URL.

- [ ] **Step 1: Add failing DTO and inline-preview API tests**

Add assertions to the detail contract and focused preview cases:

```python
assert payload["images"][0]["preview_url"].endswith(
    f"/images/{payload['images'][0]['id']}/preview"
)
assert payload["images"][0]["result_url"].endswith(
    f"/images/{payload['images'][0]['id']}/download"
)

preview = client.get(
    f"/api/batch-jobs/{job_id}/images/{image_id}/preview"
)
assert preview.status_code == 200
assert preview.content == b"annotated-only"
assert preview.headers["content-type"].startswith("image/jpeg")
assert preview.headers["content-disposition"].startswith("inline;")
assert preview.headers["x-content-type-options"] == "nosniff"
```

Cover unauthenticated access, another normal user's job, admin access, mismatched job/image IDs, incomplete image, missing file, path escape, and a storage exception containing a sentinel absolute path. Assert inaccessible combinations are 404, stable storage failures do not contain the sentinel in response or logs, and the existing single-image download remains `attachment`.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_api.py -k "detail_has_safe_result_urls or preview or single_download" -v`

Expected: FAIL because `preview_url` and the `/preview` route do not exist; existing download assertions remain green.

- [ ] **Step 3: Extend the response schema and DTO builder**

Add the field and emit only canonical same-origin paths:

```python
class BatchImageResponse(BaseModel):
    # existing fields...
    preview_url: str
    result_url: str


def image_response(job_id: int, image) -> BatchImageResponse:
    base = f"/api/batch-jobs/{job_id}/images/{image.id}"
    return BatchImageResponse(
        # existing fields...
        preview_url=f"{base}/preview",
        result_url=f"{base}/download",
    )
```

- [ ] **Step 4: Add the inline endpoint by reusing the secured result lookup**

Keep `open_result` as the single authorization/storage boundary. Add only an alias method if a name is needed for clarity; do not copy its owner, status, child-image or containment checks. Stream and close the same way as download:

```python
@router.get("/{job_id}/images/{image_id}/preview")
def preview_batch_image(job_id: int, image_id: int, request: Request,
                        user: AuthenticatedUser = Depends(current_user)):
    try:
        result = batch_service(request).open_result(user, job_id, image_id)
    except BatchServiceError as exc:
        return error_response(exc)
    disposition = f"inline; filename*=UTF-8''{quote(result.filename, safe='')}"
    return StreamingResponse(
        _stream_and_close(result.stream),
        media_type=result.media_type,
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
        },
    )
```

Do not change `/download`, ZIP generation, manifest contents or database records.

- [ ] **Step 5: Run focused backend GREEN and compatibility regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_api.py tests/test_api_compatibility.py -q`

Expected: all selected tests PASS; no local path appears in captured responses/logs; download still has attachment disposition.

- [ ] **Step 6: Commit the backend preview contract**

```powershell
git add backend/schemas.py backend/api/batch_routes.py backend/batch/service.py tests/test_batch_api.py
git commit -m "feat: add secure batch result previews"
```

---

### Task 2: Reliable Multi-Image Selection and Helmet-First Result Cards

**Files:**
- Modify: `frontend/src/api/types.ts:122-132`
- Modify: `frontend/src/batch/batchApi.ts:75-79`
- Modify: `frontend/src/batch/UploadDropzone.tsx`
- Modify: `frontend/src/batch/ResultCard.tsx`
- Modify: `frontend/src/batch/BatchPage.test.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**
- Consumes: `BatchImageResult.preview_url`, `BatchImageResult.result_url`, and `UploadDropzone.onSubmit(files: File[])`.
- Produces: `safePreviewUrl(jobId: number, imageId: number, value: string): string | null`, unchanged `safeResultUrl(...)`, one explicit file-selection button, and a result card whose primary metric is the count of `label === "no_helmet"`.

- [ ] **Step 1: Write failing upload interaction tests**

Use a spy on `HTMLInputElement.prototype.click` and real Testing Library uploads:

```tsx
const pickerClick = vi.spyOn(HTMLInputElement.prototype, "click");
await userEvent.click(screen.getByRole("button", { name: "选择图片" }));
expect(pickerClick).toHaveBeenCalledTimes(1);
expect(screen.getByLabelText("选择识别图片")).toHaveAttribute("multiple");

await userEvent.upload(input, [first, second]);
await userEvent.upload(input, [third]);
expect(screen.getByText("3 / 20")).toBeInTheDocument();

await userEvent.click(screen.getByRole("button", { name: "开始识别" }));
expect(onSubmit).toHaveBeenCalledWith([first, second, third]);
expect(pickerClick).toHaveBeenCalledTimes(1);
```

Add a rejected `createBatchJob` case and assert all selected filenames remain visible and the retry button remains enabled.

- [ ] **Step 2: Write failing result hierarchy and URL-whitelist tests**

Extend the completed image fixture with both URLs and mixed detections:

```tsx
preview_url: "/api/batch-jobs/12/images/31/preview",
result_url: "/api/batch-jobs/12/images/31/download",
detections: [
  { label: "no_helmet", confidence: 0.91, bbox: [1, 2, 30, 40] },
  { label: "person", confidence: 0.88, bbox: [0, 0, 50, 90] },
  { label: "helmet", confidence: 0.76, bbox: [50, 5, 70, 25] },
],
```

Assert the `<img>` source is `/preview`, the explicit link is `/download`, “未佩戴安全帽：1 人” appears before contextual detections, and a non-canonical external or mismatched preview URL renders no image.

- [ ] **Step 3: Run frontend RED tests**

Run: `npm run test:run -- frontend/src/batch/BatchPage.test.tsx`

Expected: FAIL because the outer dropzone/input click bubbles, `preview_url` is not typed or used, and there is no violation-first summary.

- [ ] **Step 4: Replace the nested clickable input with one explicit trigger**

Keep drag/drop on a non-clicking container and use one button for mouse and keyboard activation:

```tsx
<div
  className="batch-dropzone"
  onDragOver={(event) => event.preventDefault()}
  onDrop={drop}
>
  <button
    type="button"
    className="batch-picker"
    disabled={pending}
    onClick={() => input.current?.click()}
  >
    选择图片
  </button>
  <input
    ref={input}
    className="sr-only"
    aria-label="选择识别图片"
    type="file"
    accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
    multiple
    disabled={pending}
    onChange={change}
  />
</div>
```

Remove the outer `role="button"`, outer `onClick`, and custom Enter/Space handler. Preserve existing atomic validation, append semantics, dedupe key, object URL cleanup, 20-image/10-MiB limits and input value reset. Do not clear `items` in the mutation error path.

- [ ] **Step 5: Add typed canonical preview validation**

```ts
export function safePreviewUrl(jobId: number, imageId: number, value: string) {
  const expected = `/api/batch-jobs/${jobId}/images/${imageId}/preview`;
  return value === expected ? value : null;
}
```

Add `preview_url: string` to `BatchImageResult`; leave `safeResultUrl` limited to `/download`.

- [ ] **Step 6: Render the preview and violation-first result hierarchy**

Derive, never mutate, the persisted detection array:

```tsx
const violations = image.detections.filter((item) => item.label === "no_helmet");
const context = image.detections.filter((item) => item.label !== "no_helmet");
const previewUrl = image.status === "completed"
  ? safePreviewUrl(jobId, image.id, image.preview_url)
  : null;
```

Render the preview URL in `<img>`, a primary `未佩戴安全帽：{violations.length} 人` summary, violation confidences in the emphasized list, contextual detections in a muted list, and keep `result_url` only on the secondary download link. Add an image `onError` state with a retry button that changes a local retry key/query suffix only for the canonical preview URL; it must never fall back to the attachment URL.

- [ ] **Step 7: Add focused styles without changing the global visual system**

Add scoped classes for `.batch-picker`, `.batch-result__violation-summary`, `.batch-detections--violation`, `.batch-detections--context`, and `.batch-result__preview-error`. Reuse existing color tokens; use danger/red for violations, neutral text for context, visible keyboard focus, and responsive image sizing.

- [ ] **Step 8: Run frontend GREEN, typecheck and production build**

Run:

```powershell
npm run test:run -- frontend/src/batch/BatchPage.test.tsx
npm run typecheck
npm run build
```

Expected: focused tests PASS, TypeScript exits 0, and Vite rebuilds `frontend/dist` successfully under Node 24.x.

- [ ] **Step 9: Commit the upload and result UI**

```powershell
git add frontend/src/api/types.ts frontend/src/batch/batchApi.ts frontend/src/batch/UploadDropzone.tsx frontend/src/batch/ResultCard.tsx frontend/src/batch/BatchPage.test.tsx frontend/src/styles/global.css frontend/dist
git commit -m "fix: streamline helmet batch recognition"
```

---

### Task 3: Shared Helmet-Priority Annotation for Batch and Live Frames

**Files:**
- Modify: `backend/batch/annotate.py`
- Modify: `backend/pipeline/runtime.py:151-193`
- Modify: `tests/test_batch_worker.py:156-166`
- Modify: `tests/test_runtime_security.py`

**Interfaces:**
- Consumes: `annotate(image: np.ndarray, detections: list[Detection]) -> np.ndarray`, `DetectorProvider.predict_realtime(...)`, and `EventHub.broadcast_bytes(channel: str, payload: bytes)`.
- Produces: deterministic `_style(label: str) -> AnnotationStyle` (or equivalent private structure) with `no_helmet` red/width 4, `person` blue-gray/width 2, `helmet` muted green/width 2; live preview broadcasts the annotated JPEG while `RuleEngine.evaluate` receives the unchanged detections.

- [ ] **Step 1: Add failing annotation-priority tests**

Create a white image with separated class boxes and inspect pixels at each border. Assert the `no_helmet` border has the exact configured red RGB color and four-pixel thickness, while `person` and `helmet` use distinct subdued colors and two-pixel thickness. Keep the existing deterministic/clamping assertion.

```python
detections = [
    Detection("no_helmet", 0.95, (4, 4, 20, 20)),
    Detection("person", 0.80, (28, 4, 44, 20)),
    Detection("helmet", 0.88, (52, 4, 68, 20)),
]
annotated = annotate(np.full((32, 76, 3), 255, dtype=np.uint8), detections)
assert tuple(annotated[4, 4]) == expected_no_helmet_bgr
assert tuple(annotated[7, 4]) == expected_no_helmet_bgr
```

- [ ] **Step 2: Add a failing real-time annotation flow test**

Use fake repository/provider/rules/hub objects, call `_process_frame`, decode the broadcast JPEG, and assert it differs from the raw frame at the `no_helmet` box. Also assert `rules.evaluate` receives the same detection objects and its existing ROI/allowed-class inputs, so alert behavior is unchanged.

- [ ] **Step 3: Run the focused RED tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_worker.py tests/test_runtime_security.py -k "annotation or preview" -v`

Expected: style assertions FAIL because colors are hash-derived and the live broadcast is the unannotated frame.

- [ ] **Step 4: Introduce explicit safe-helmet annotation styles**

Replace only the three known-class hash styles; retain deterministic fallback colors:

```python
@dataclass(frozen=True)
class AnnotationStyle:
    color: tuple[int, int, int]  # RGB for Pillow
    width: int


_STYLES = {
    "no_helmet": AnnotationStyle((220, 38, 38), 4),
    "person": AnnotationStyle((86, 112, 138), 2),
    "helmet": AnnotationStyle((76, 138, 102), 2),
}
```

Use `style.width` for the rectangle and `style.color` for border/label. Continue clamping non-finite/out-of-range coordinates and returning BGR output.

- [ ] **Step 5: Annotate the real-time preview without changing rule inputs**

Change `_process_frame` and `_publish_preview` only at the rendering boundary:

```python
detections = self.detector_provider.predict_realtime(frame.image).detections
events = self.rules.evaluate(..., detections, ...)
self._publish_preview(frame, detections)

def _publish_preview(self, frame: Frame, detections: list[Detection]) -> None:
    rendered = annotate(frame.image, detections)
    ok, encoded = cv2.imencode(".jpg", rendered, [...])
```

Do not write the rendered pixels back to `frame.image`; snapshots and event persistence remain on their current contract unless an existing test explicitly establishes otherwise.

- [ ] **Step 6: Run focused GREEN and rules regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_worker.py tests/test_runtime_security.py tests/test_rules.py -q`

Expected: all tests PASS; `no_helmet` style is visually dominant; rules/event counts remain unchanged.

- [ ] **Step 7: Commit the shared annotation behavior**

```powershell
git add backend/batch/annotate.py backend/pipeline/runtime.py tests/test_batch_worker.py tests/test_runtime_security.py
git commit -m "feat: emphasize helmet violations in inference previews"
```

---

### Task 4: Documentation and End-to-End Local SQLite Verification

**Files:**
- Modify: `README.md:170-210`
- Test: `tests/test_batch_end_to_end.py`
- Test: all existing Python and frontend suites

**Interfaces:**
- Consumes: the preview endpoint, frontend built assets, shared active `best.pt` model, and documented local SQLite single-instance runtime.
- Produces: user-facing operating instructions and final evidence that upload, worker, preview, download, live annotation and existing event rules work together.

- [ ] **Step 1: Update README behavior and testing instructions**

Document that one Windows picker operation can select multiple files with Ctrl/Shift, additional selections append up to 20, upload errors retain selections, completed annotated images display directly, and downloads occur only from explicit download actions. State that `no_helmet` is the primary violation count while `person`/`helmet` remain contextual evidence. Keep the existing Node 24, model path, SQLite local-only and MySQL production boundaries unchanged.

- [ ] **Step 2: Extend the real-model smoke assertion**

In `tests/test_batch_end_to_end.py`, after the existing two-image job completes, request each `preview_url` and assert JPEG content plus `inline`/`nosniff`; separately request `result_url` and assert `attachment`. Assert the API still reports all detections and derives a `no_helmet` count in the test without assuming a fixed count from every future model revision.

- [ ] **Step 3: Run the targeted real-model and feature regressions**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_batch_api.py tests/test_batch_worker.py tests/test_runtime_security.py tests/test_rules.py -q
.venv\Scripts\python.exe -m pytest tests/test_batch_end_to_end.py -q
npm run test:run -- frontend/src/batch/BatchPage.test.tsx frontend/src/live/LivePage.test.tsx
```

Expected: all selected tests PASS; the real smoke uses `runs/detect/train-3/weights/best.pt` and two known construction-PPE images when those local assets are present.

- [ ] **Step 4: Run complete local SQLite verification**

Run:

```powershell
.venv\Scripts\python.exe -m pytest -q
npm run test:run
npm run typecheck
npm run build
.venv\Scripts\python.exe -m compileall -q backend training tests
git diff --check
```

Expected: Python and frontend suites have zero failures; only the already documented live-MySQL and platform-symlink tests may skip; typecheck/build/compileall/diff-check exit 0.

- [ ] **Step 5: Perform manual local browser acceptance**

Start the FastAPI service with local SQLite and the active trained model, then verify: one click opens one picker; selecting at least two files in one dialog lists both; reopening appends; “开始识别” does not open the picker; completed images render inline; “未佩戴安全帽” is the first metric; explicit single-image and ZIP download buttons still download; live frames show red thick `no_helmet` boxes. Record any unavailable browser automation as a concern instead of claiming it passed.

- [ ] **Step 6: Commit documentation and end-to-end evidence**

```powershell
git add README.md tests/test_batch_end_to_end.py frontend/dist
git commit -m "docs: complete helmet inference workflow"
```

---

## Self-Review Record

- Spec coverage: upload event isolation, one-time and repeated multi-select, failure retention, 20/10-MiB limits, inline preview security, owner/admin isolation, canonical URL checks, `no_helmet` priority, contextual detections, unchanged downloads/ZIP/rules, real-time annotation, docs and full regression all map to explicit tasks.
- Scope: no migration, model retraining, class remapping, event-rule change, MySQL acceptance or deployment step was introduced.
- Placeholder scan: no `TBD`, `TODO`, “implement later”, undefined “similar to” steps or unspecified test requests remain.
- Type consistency: backend `preview_url` maps to frontend `BatchImageResult.preview_url`; `safePreviewUrl` accepts the same `(jobId, imageId, value)` shape as `safeResultUrl`; runtime passes `list[Detection]` to the unchanged public `annotate` function.
- Security consistency: preview and download share `BatchService.open_result`; frontend does not accept arbitrary object URLs; errors and logs remain path-free.
