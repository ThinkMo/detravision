# Training Scheduler Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add database leases, task ownership generations, heartbeats, and safe recovery to the local training scheduler without exposing internal process data.

**Architecture:** Keep the same-host file lock, then acquire a database-backed singleton lease before claiming jobs. Every active-job mutation is guarded by an owner token and execution generation; expired heartbeats are recovered only by the current lease owner after the grace interval.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite/MySQL, pytest, React/TypeScript.

**Spec:** `docs/superpowers/specs/2026-09-06-training-scheduler-reliability-design.md`

## Global Constraints

- Preserve SQLite single-instance behavior and MySQL compatibility.
- Never expose owner tokens, PIDs, lease revisions, absolute paths, or raw exceptions.
- Do not automatically resume interrupted training.
- `TRAINING_LEASE_SECONDS >= 3 * TRAINING_HEARTBEAT_SECONDS`.
- `TRAINING_RECOVERY_GRACE_SECONDS >= TRAINING_LEASE_SECONDS`.
- Use TDD and commit each independently testable task.

---

### Task 1: Persist Scheduler Lease and Job Ownership

**Files:**
- Create: `migrations/versions/0010_training_scheduler_lease.py`
- Modify: `backend/db/models.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- Produces `TrainingScheduler.owner_id`, `heartbeat_at`, `lease_expires_at`, `revision`.
- Produces `TrainingJob.owner_id`, `heartbeat_at`, `execution_generation`.

- [ ] Add failing SQLite upgrade and offline MySQL DDL assertions for all fields and legacy defaults.
- [ ] Run `pytest tests/test_migrations.py -q` and confirm the new assertions fail.
- [ ] Add migration `0010` and matching ORM columns; seed the singleton scheduler row without changing historical job states.
- [ ] Run `pytest tests/test_migrations.py -q` and confirm it passes.
- [ ] Commit `feat: persist training scheduler leases`.

### Task 2: Implement Atomic Lease Repository Contract

**Files:**
- Modify: `backend/training/repository.py`
- Modify: `tests/test_training_repository.py`

**Interfaces:**
- Produces `acquire_lease(owner_id, lease_seconds) -> int | None` returning the new/current revision.
- Produces `renew_lease(owner_id, revision, lease_seconds) -> bool`.
- Produces `release_lease(owner_id, revision) -> bool`.
- Produces `scheduler_state() -> str`.
- Changes `claim_next(owner_id, revision) -> dict | None` to stamp ownership and increment generation.
- Changes active mutations to accept `owner_id` and `execution_generation` guards.
- Produces `recover_expired(owner_id, revision, grace_seconds) -> int`.

- [ ] Add failing repository tests for acquire/renew/release, expiry takeover, stale-owner rejection, generation increments, guarded progress/terminal updates, and grace-based recovery.
- [ ] Run the focused repository tests and confirm failure.
- [ ] Implement the lease and guarded mutation contract with SQLite writer serialization and MySQL row locks.
- [ ] Keep public `training_job_dict()` free of internal owner/PID fields; add only safe runtime state when requested by the service.
- [ ] Run repository, migration, and MySQL-offline tests.
- [ ] Commit `feat: guard training jobs with database leases`.

### Task 3: Integrate Lease Heartbeats into the Manager

**Files:**
- Modify: `backend/core/config.py`
- Modify: `.env.example`
- Modify: `backend/training/manager.py`
- Modify: `tests/test_training_manager.py`
- Modify: `tests/test_app_startup.py`

**Interfaces:**
- Manager generates one owner token per `start()` lifecycle.
- Manager acquires the file lock, then the database lease, then recovers expired jobs.
- Manager renews scheduler and task heartbeats during every bounded monitor cycle.

- [ ] Add failing configuration validation and manager lifecycle tests.
- [ ] Add deterministic tests for two managers, lease loss, stale updates, shutdown release, process disappearance, and the previously flaky takeover observation.
- [ ] Run focused tests and confirm failure.
- [ ] Implement configuration, owner lifecycle, heartbeat renewal, isolation, and conditional cleanup.
- [ ] Run manager/startup/config tests repeatedly, including ten repetitions of the former takeover race.
- [ ] Commit `feat: add training heartbeats and safe recovery`.

### Task 4: Expose Safe Scheduler Health

**Files:**
- Modify: `backend/api/training_routes.py`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/admin/models/ModelsPage.tsx`
- Modify: `frontend/src/admin/models/TrainingJobsPanel.tsx`
- Modify: `frontend/src/admin/models/ModelsPage.test.tsx`
- Modify: `tests/test_training_api.py`

**Interfaces:**
- Catalog/list response exposes `scheduler_state: healthy | unavailable | disabled`.
- Job response exposes `runtime_state` and `recovery_hint` only.

- [ ] Add failing API and component tests proving safe fields, no internal leakage, queue creation through a non-owner API instance, and disabled/unavailable UI messages.
- [ ] Run focused tests and confirm failure.
- [ ] Implement safe DTO derivation and frontend status rendering.
- [ ] Run backend/frontend focused tests and typecheck.
- [ ] Commit `feat: report training scheduler health`.

### Task 5: Reliability Regression Gate and Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/training-guide.md`
- Modify: `docs/superpowers/plans/2026-09-06-training-scheduler-reliability.md`

- [ ] Document lease/heartbeat configuration, recovery semantics, and multi-instance behavior.
- [ ] Run the backend suite, frontend suite, typecheck, build, and `git diff --check`.
- [ ] Confirm the public API does not contain `owner_id`, `execution_generation`, lease revision, PID, or absolute paths.
- [ ] Mark plan steps complete and commit `docs: explain reliable training recovery`.
