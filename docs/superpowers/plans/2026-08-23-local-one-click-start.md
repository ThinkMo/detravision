# Local One-click Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide one-command local startup on Windows and Linux, including first-run environment preparation, frontend build, optional `.env` loading, and FastAPI startup.

**Architecture:** `start.cmd` and `start.sh` are thin platform entry points. Both delegate to a standard-library Python launcher in `scripts/start_local.py`, which validates prerequisites, prepares only missing dependencies, loads `.env` without overriding the process environment, builds the frontend, and runs Uvicorn in the foreground.

**Tech Stack:** Python 3.12+, Windows CMD, POSIX shell, npm/Node.js 24, pytest.

**Spec:** Approved chat design from 2026-08-23.

## Global Constraints

- Local mode uses SQLite and one batch worker; this launcher is not a production/MySQL deployment tool.
- Existing environment variables override `.env`; secrets are never printed.
- Subprocesses use argument arrays rather than shell interpolation.
- First run prepares missing dependencies; `--skip-install` and `--skip-build` provide fast paths.

---

### Task 1: Shared launcher, platform entry points, and documentation

**Files:**
- Create: `scripts/start_local.py`
- Create: `scripts/__init__.py`
- Create: `start.cmd`
- Create: `start.sh`
- Create: `tests/test_local_start.py`
- Modify: `README.md`
- Modify: `docs/README.md`

**Interfaces:**
- Produces: `load_dotenv(path, environ)`, `build_startup_plan(...)`, and CLI flags `--skip-install`, `--skip-build`, `--host`, `--port`.
- Produces: platform launch commands that resolve the repository root independently of the caller's current directory.

- [x] **Step 1: Write failing launcher behavior tests**

Test environment precedence, safe parsing, Windows/Linux virtual-environment paths, first-run command planning, skip flags, and host/port validation through real pure functions.

- [x] **Step 2: Run tests and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_local_start.py -q`

Expected: collection failure because `scripts.start_local` does not exist.

- [x] **Step 3: Implement the minimal shared launcher**

Use `pathlib`, `subprocess`, `argparse`, and `os.environ`; create `.venv` only when absent, install only when requested or dependency directories are missing, build unless skipped, then replace the launcher process with foreground Uvicorn semantics via `subprocess.call`.

- [x] **Step 4: Add thin Windows/Linux entry points and update documentation**

Document first-run behavior, flags, prerequisites, URLs, `.env` precedence, Ctrl+C shutdown, and the SQLite-only local boundary.

- [x] **Step 5: Verify**

Run focused pytest, `bash -n start.sh` when Bash is available, Python compilation, `git diff --check`, and launcher `--help`.

- [x] **Step 6: Commit**

Commit message: `feat: add cross-platform local launcher`
