FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend backend
COPY training training
RUN mkdir -p /app/data/snapshots /app/model_repo

ENV DATABASE_PATH=/app/data/safety_detect.db
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
