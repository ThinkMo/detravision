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
