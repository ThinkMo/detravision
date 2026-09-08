FROM node:24-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    UPLOAD_DIR=/app/data/uploads \
    SNAPSHOT_DIR=/app/data/snapshots \
    MODEL_REPO_DIR=/app/model_repo \
    TRAINING_RUN_DIR=/app/data/training-runs \
    TRAINING_MODEL_CACHE_DIR=/app/data/training-models \
    TRAINING_DATASET_ROOTS=default=/app/datasets

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY migrations/ migrations/
COPY backend/ backend/
COPY training/ training/
COPY deploy/entrypoint.sh /usr/local/bin/detravision-entrypoint
COPY --from=frontend-builder /build/frontend/dist frontend/dist/

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/data /app/model_repo /app/datasets \
    && chown -R app:app /app /usr/local/bin/detravision-entrypoint \
    && sed -i 's/\r$//' /usr/local/bin/detravision-entrypoint \
    && chmod 755 /usr/local/bin/detravision-entrypoint

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

ENTRYPOINT ["/usr/local/bin/detravision-entrypoint"]
