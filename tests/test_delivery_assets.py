import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_distribution_declares_agpl_license_and_product_identity():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))

    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in license_text
    assert "Version 3, 19 November 2007" in license_text
    assert package["name"] == "detravision"
    assert package["license"] == "AGPL-3.0-only"
    assert package["repository"]["url"] == "https://github.com/ThinkMo/detravision.git"


def test_python_direct_dependencies_are_exactly_pinned():
    for filename in ("requirements.txt", "requirements-dev.txt"):
        lines = [line.strip() for line in (ROOT / filename).read_text(encoding="utf-8").splitlines()]
        dependencies = [line for line in lines if line and not line.startswith(("#", "-r"))]
        assert dependencies
        assert all("==" in dependency for dependency in dependencies)


def test_container_builds_frontend_runs_as_non_root_and_migrates_before_start():
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "deploy" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "FROM node:24" in dockerfile
    assert "npm ci" in dockerfile and "npm run build" in dockerfile
    assert "USER app" in dockerfile
    assert "frontend/dist" in dockerfile
    assert "run_migrations" in entrypoint
    assert 'exec uvicorn backend.main:app' in entrypoint


def test_compose_uses_mysql_healthchecks_and_persistent_application_data():
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "mysql:8.4" in compose
    assert "condition: service_healthy" in compose
    assert "DATABASE_URL" in compose
    assert "app-data:/app/data" in compose
    assert "healthcheck:" in compose


def test_ci_runs_backend_frontend_and_container_gates():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for expected in ("python-version: '3.12'", "node-version: '24'", "pytest", "npm run test:run", "npm run typecheck", "npm run build", "docker build"):
        assert expected in workflow
