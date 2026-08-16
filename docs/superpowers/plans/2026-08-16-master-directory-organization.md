# Master Directory Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清理 `master` 的可再生成缓存并建立准确的源码、文档和本地产物目录边界，同时保持所有现有命令和导入路径不变。

**Architecture:** 不迁移任何源码目录，只通过 `.gitignore`、README 目录树和文档索引定义边界；缓存清理仅针对可再生成路径。已注销 worktree 的 ACL 残留使用精确路径清理，权限不足时保留并给出管理员命令。

**Tech Stack:** Git、PowerShell、Python 3.12、pytest、Node.js 24、TypeScript。

## Global Constraints

- 不移动或重命名 `backend/`、`frontend/`、`training/`、`tests/`、`migrations/`、`deploy/`。
- 不改变 Python 导入、FastAPI 启动、训练 CLI、Alembic 或前端命令。
- 不删除 `data/`、`datasets/`、`runs/`、`.venv/`、`frontend/node_modules/`、`frontend/dist/`、数据库、上传文件或模型权重。
- 仅清理 `.pytest_cache/`、`__pycache__/`、`*.pyc`、临时 `*.log` 和已注销 worktree 残留。
- `.worktrees/web-console` 只能以精确绝对路径处理；ACL 失败必须如实报告，不能扩大删除范围。
- `model_repo/.gitkeep` 必须继续受版本控制。

---

### Task 1: Define Ignored Artifact Boundaries

**Files:**
- Modify: `.gitignore`
- Test: Git ignore behavior through `git check-ignore`

**Interfaces:**
- Consumes: existing local directory layout and tracked `model_repo/.gitkeep`.
- Produces: ignore rules for logs, coverage, static-analysis caches and editor metadata without hiding source files.

- [ ] **Step 1: Verify the missing ignore cases**

Create no files. Pipe representative paths into Git:

```powershell
@(
  "service.log",
  ".coverage",
  ".coverage.worker",
  "htmlcov/index.html",
  ".mypy_cache/state.json",
  ".ruff_cache/state.json",
  ".idea/workspace.xml",
  ".vscode/settings.json"
) | git check-ignore --stdin
```

Expected RED: one or more representative paths are absent from output because the new rules do not exist.

- [ ] **Step 2: Add exact ignore rules**

Append:

```gitignore
*.log
.coverage
.coverage.*
htmlcov/
.mypy_cache/
.ruff_cache/
.idea/
.vscode/
```

- [ ] **Step 3: Verify ignore behavior and tracked boundaries**

Run the Step 1 command again and require all eight paths in output. Then run:

```powershell
git check-ignore backend/main.py tests/test_api_compatibility.py docs/training-guide.md
git ls-files model_repo/.gitkeep backend/main.py frontend/src/main.tsx training/train.py
```

Expected: `git check-ignore` emits nothing for source/docs; `git ls-files` emits all four tracked files.

- [ ] **Step 4: Commit the ignore boundary**

```powershell
git add .gitignore
git commit -m "chore: clarify local artifact ignores"
```

---

### Task 2: Document the Project Layout

**Files:**
- Create: `docs/README.md`
- Modify: `README.md:50-85`

**Interfaces:**
- Consumes: current tracked top-level directories and `docs/training-guide.md`.
- Produces: an accurate root directory tree and a stable documentation index.

- [ ] **Step 1: Record the current tracked top-level inventory**

Run:

```powershell
git ls-files | ForEach-Object { ($_ -split '/')[0] } | Group-Object | Sort-Object Name
```

Expected: the inventory includes backend, frontend, training, tests, migrations, deploy, docs and model_repo.

- [ ] **Step 2: Create the documentation index**

Write `docs/README.md` with sections for:

```markdown
# 项目文档

## 使用指南
- [训练集构建与模型训练](training-guide.md)

## 设计规格
- 链接 `superpowers/specs/` 下每个已跟踪设计文档

## 实施计划
- 链接 `superpowers/plans/` 下每个已跟踪实施计划
```

Explain that specs/plans are engineering records and README/training guide are the normal user entry points.

- [ ] **Step 3: Replace the root README directory tree**

Show all stable tracked boundaries:

```text
backend/      FastAPI、鉴权、数据库、实时与批量推理
frontend/     React/Vite 管理控制台
training/     数据转换、训练、验证和导出 CLI
tests/        Python 自动化测试
migrations/   Alembic 数据库迁移
deploy/       Dockerfile 与 Compose
docs/         用户指南、设计规格和实施计划
model_repo/   本地导出模型落点
```

Add a separate local-only list for `data/`、`datasets/`、`runs/`、`.venv/`、`frontend/node_modules/` and `frontend/dist/`. Do not alter existing quick-start commands.

- [ ] **Step 4: Validate documentation links and formatting**

Run:

```powershell
rg -n "docs/README.md|training-guide.md|backend/|frontend/|migrations/|data/|datasets/|runs/" README.md docs/README.md
git diff --check
```

Expected: all key directories and training guide are linked; diff check exits 0.

- [ ] **Step 5: Commit the documentation index**

```powershell
git add README.md docs/README.md
git commit -m "docs: organize project directory guide"
```

---

### Task 3: Clean Regenerable Caches and Verify Master

**Files:**
- Delete only ignored cache/log files under `D:\free`
- Attempt exact cleanup: `D:\free\.worktrees\web-console`

**Interfaces:**
- Consumes: ignore boundary from Task 1 and exact worktree state from Git.
- Produces: clean registered worktree state and verified unchanged code/tool entry points.

- [ ] **Step 1: Resolve every cleanup target before deletion**

Use `Get-ChildItem` only under `D:\free` to list `.pytest_cache`, `__pycache__`, `*.pyc`, and `*.log`. For every resolved target, require `StartsWith('D:\free\')`; exclude `data/`, `datasets/`, `runs/`, `.venv/`, `frontend/node_modules/`, `frontend/dist/` and `.git/`.

- [ ] **Step 2: Delete only validated cache/log targets**

Use `Remove-Item -LiteralPath` on the validated explicit paths in PowerShell. Do not use a wildcard recursive delete rooted at `D:\free`.

- [ ] **Step 3: Attempt stale worktree cleanup safely**

First require:

```powershell
git worktree list --porcelain
git branch --list feature/web-console
```

Only if Git lists no `web-console` worktree and no feature branch, attempt deletion of exactly `D:\free\.worktrees\web-console`. If ACL denies access, stop attempts and report this administrator command:

```powershell
takeown /F "D:\free\.worktrees\web-console" /R /D Y
icacls "D:\free\.worktrees\web-console" /grant "$env:USERNAME:(OI)(CI)F" /T /C
Remove-Item -LiteralPath "D:\free\.worktrees\web-console" -Recurse -Force
```

- [ ] **Step 4: Run final structural verification**

Run:

```powershell
D:\free\.venv\Scripts\python.exe -m compileall -q backend training tests
D:\free\.venv\Scripts\python.exe -m pytest tests/test_train_args.py tests/test_prepare_construction_ppe.py tests/test_api_compatibility.py -q
npm run typecheck
git diff --check
git status --short
git worktree list
```

Use Node.js 24 for `npm run typecheck`. Expected: compile/typecheck/diff pass, focused Python tests pass, Git registers only `D:/free [master]`, and status is clean after commits.

- [ ] **Step 5: Record cleanup outcome**

No extra tracked report is required. Final response must name deleted cache classes, confirm protected data was retained, and disclose whether the ACL-protected stale directory remains.

---

## Self-Review Record

- Spec coverage: ignore rules, README tree, docs index, safe cache cleanup, exact worktree handling and verification each have a task.
- Scope: no source, migration, command, data, model or dependency directory moves are planned.
- Placeholder scan: no `TBD`, `TODO`, “later” or undefined implementation steps remain.
- Interface consistency: all paths and commands use the existing root layout; `docs/training-guide.md` and `model_repo/.gitkeep` remain unchanged.
