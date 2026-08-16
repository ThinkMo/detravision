# Task 2 Report: ORM Repositories and Existing API Compatibility

## Implementation

- Replaced the SQLite `Database` class with `backend.db.repositories.Repository`, backed by SQLAlchemy sessions from Task 1.
- Added explicit aggregate operations for cameras, people, alarm groups and members, events, and model versions. No public repository method accepts a table name or SQL `where` clause.
- Added JSON-ready ORM converters. They decode JSON storage fields, return booleans as booleans, serialize timestamps as UTC ISO-8601 strings, and retain both `detection_event.ack_by` and `detection_event.ack_user_id`.
- Ported FastAPI routes and `RuntimeManager` to `Repository`. API routes retain their paths, status codes, and dictionary response boundaries. Model activation resets all active flags and activates the requested model inside one transaction.
- Application startup now runs Alembic migrations, creates the SQLAlchemy engine/session factory, and disposes the engine during shutdown.
- Removed `backend/db/database.py`.

## Files

- Created: `backend/db/repositories.py`
- Created: `tests/test_api_compatibility.py`
- Modified: `backend/db/__init__.py`, `backend/api/routes.py`, `backend/pipeline/runtime.py`, `backend/main.py`, `tests/test_database.py`
- Deleted: `backend/db/database.py`

## TDD evidence

The test production changes were named before the tests were written:

- Camera persistence dropping JSON values or CRUD state transitions.
- Model activation leaving a previous model active.
- ORM migration changing the existing camera HTTP response keys or status codes.

RED command, run before repository implementation:

```powershell
& 'D:\free\.venv\Scripts\python.exe' -m pytest tests/test_database.py tests/test_api_compatibility.py -v
```

RED result: collection failed as expected with `ImportError: cannot import name 'Repository' from 'backend.db'` in both test modules; the explicit repository and FastAPI repository dependency did not yet exist.

GREEN command, after the focused implementation:

```powershell
& 'D:\free\.venv\Scripts\python.exe' -m pytest tests/test_database.py tests/test_api_compatibility.py -v
```

GREEN result: `3 passed, 1 warning`. The warning is FastAPI/TestClient's upstream Starlette deprecation warning for the installed `httpx`; it is unrelated to this change.

## Verification

Requested regression subset:

```powershell
& 'D:\free\.venv\Scripts\python.exe' -m pytest tests/test_database.py tests/test_api_compatibility.py tests/test_rules.py tests/test_frame_queue.py -v
```

Result: `6 passed, 1 warning`.

Full suite, run before committing:

```powershell
& 'D:\free\.venv\Scripts\python.exe' -m pytest -v
```

Result: `38 passed, 1 skipped, 1 warning in 5.09s`. The skipped test requires `TEST_MYSQL_URL`; the warning is the existing FastAPI/TestClient deprecation warning above.

## Self-review

- Confirmed no runtime/API caller imports `backend.db.database` or calls a generic table-name repository operation.
- Confirmed route response conversion is JSON-ready and that camera create/list/update/delete preserve their legacy status codes and response-key shape.
- Confirmed event dictionaries retain legacy `ack_by` plus the new nullable `ack_user_id`.
- Confirmed `activate_model` performs the clear-and-activate transition in one session transaction.
- `git diff --check` returned no whitespace errors.

## Fix round 1/5: review findings

### Implementation

- Made `Repository.replace_group_members` preserve legacy `executemany([], ...)` behavior: it always removes prior members, but only issues an INSERT when the replacement list is non-empty. Empty alarm groups now create successfully.
- Expanded real ASGI API contract coverage from cameras alone to persons, alarm groups, events, and models. The tests cover every existing resource route: list/create/update/delete where provided, and model activation where that is the provided mutation path.
- Added `@pytest.mark.mysql` repository CRUD parity coverage in `tests/test_database.py`. It uses `TEST_MYSQL_URL`, runs migrations and the same JSON camera create/update/read/delete behavior against MySQL, and skips when the URL is absent. Per the binding human ruling, live MySQL execution is deferred to Task 7; no Docker or MySQL service was installed or started for Task 2.
- Removed the FastAPI/TestClient warning at its source in our tests by replacing `fastapi.testclient.TestClient` with `httpx.AsyncClient` plus `httpx.ASGITransport`. This still exercises the real FastAPI application in-process and requires no warning filter.

### TDD RED/GREEN evidence

Before adding the tests, the production changes named were: an empty membership replacement issuing a parameterless INSERT; API response contracts drifting for non-camera resources; and the deprecated TestClient import causing warning output.

RED command:

```powershell
& 'D:\free\.venv\Scripts\python.exe' -m pytest tests/test_api_compatibility.py tests/test_database.py -v
```

RED result: `1 failed, 6 passed, 1 skipped, 2 warnings`. `test_alarm_group_routes_allow_empty_members_and_preserve_response_keys` received HTTP `409` instead of `201`, with SQLAlchemy warnings showing a parameterless insert into the composite-primary-key membership table. The new route coverage passed against current behavior; only empty member replacement required a production correction.

GREEN command:

```powershell
& 'D:\free\.venv\Scripts\python.exe' -m pytest tests/test_api_compatibility.py tests/test_database.py -v
```

GREEN result: `7 passed, 1 skipped in 2.18s`, with no warnings. The skipped case is the new marker-controlled MySQL parity test because `TEST_MYSQL_URL` is not configured.

Warning reproduction command:

```powershell
& 'D:\free\.venv\Scripts\python.exe' -W default -c "from fastapi.testclient import TestClient"
```

It reproduced exactly: `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` The warning originates in the installed `fastapi.testclient` re-export and Starlette's `httpx` fallback, not in an API request. Our tests no longer import that deprecated adapter, so no suppression was necessary.

### Fix-round test files and self-review

- Updated `backend/db/repositories.py`, `tests/test_api_compatibility.py`, and `tests/test_database.py`.
- Confirmed an empty `person_ids` request returns `201` and `members: []`, while non-empty replacement remains explicit.
- Confirmed tests assert serialized API dictionaries and status codes, rather than mock interactions.
- Confirmed marker-controlled MySQL test is safe to skip locally and requires only the externally supplied `TEST_MYSQL_URL` when Task 7 runs it.
- Confirmed the TestClient import was removed rather than hidden with a broad warning filter.

Full-suite command, run before committing this fix round:

```powershell
& 'D:\free\.venv\Scripts\python.exe' -m pytest -v
```

Full-suite result: `42 passed, 2 skipped in 5.89s`, with no warnings. Both skipped tests require `TEST_MYSQL_URL`; their live execution is deferred to Task 7 by the human ruling.
