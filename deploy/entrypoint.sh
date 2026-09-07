#!/bin/sh
set -eu

python -c "from backend.core.config import settings; from backend.db.migrate import run_migrations; run_migrations(settings.database_url)"
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
