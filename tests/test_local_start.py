from __future__ import annotations

from pathlib import Path

import pytest

from scripts.start_local import (
    StartupError,
    apply_local_defaults,
    build_startup_plan,
    load_dotenv,
    validate_node_version,
    venv_python,
)


def test_load_dotenv_preserves_process_values_and_parses_safe_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# local settings\n"
        "DATABASE_URL=sqlite:///custom.db\n"
        "DISPLAY_NAME=\"DetraVision Local\"\n"
        "EMPTY=\n"
        "export MODEL_PATH='runs/model best.pt'\n"
        "INVALID LINE\n",
        encoding="utf-8",
    )
    environment = {"DATABASE_URL": "sqlite:///from-process.db"}

    loaded = load_dotenv(env_file, environment)

    assert loaded == {"DISPLAY_NAME", "EMPTY", "MODEL_PATH"}
    assert environment == {
        "DATABASE_URL": "sqlite:///from-process.db",
        "DISPLAY_NAME": "DetraVision Local",
        "EMPTY": "",
        "MODEL_PATH": "runs/model best.pt",
    }


@pytest.mark.parametrize(
    ("platform_name", "expected"),
    [
        ("win32", Path(".venv/Scripts/python.exe")),
        ("linux", Path(".venv/bin/python")),
    ],
)
def test_venv_python_uses_platform_layout(
    tmp_path: Path, platform_name: str, expected: Path
) -> None:
    assert venv_python(tmp_path, platform_name) == tmp_path / expected


def test_first_run_plan_prepares_dependencies_builds_and_starts(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (tmp_path / "frontend").mkdir()

    plan = build_startup_plan(
        root=tmp_path,
        bootstrap_python=Path("python3"),
        platform_name="linux",
        skip_install=False,
        skip_build=False,
        host="127.0.0.1",
        port=8000,
    )

    assert [step.name for step in plan] == [
        "create-venv",
        "install-python",
        "install-frontend",
        "build-frontend",
        "start-server",
    ]
    assert plan[0].args == ("python3", "-m", "venv", str(tmp_path / ".venv"))
    assert plan[-1].args[-4:] == ("--host", "127.0.0.1", "--port", "8000")
    assert plan[-1].cwd == tmp_path


def test_existing_dependencies_skip_install_but_rebuild_frontend(tmp_path: Path) -> None:
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").touch()
    (tmp_path / "frontend/node_modules").mkdir(parents=True)
    (tmp_path / "frontend/package-lock.json").touch()

    plan = build_startup_plan(
        root=tmp_path,
        bootstrap_python=Path("python3"),
        platform_name="linux",
        skip_install=False,
        skip_build=False,
        host="0.0.0.0",
        port=9000,
    )

    assert [step.name for step in plan] == ["build-frontend", "start-server"]


def test_skip_install_reports_missing_environment(tmp_path: Path) -> None:
    (tmp_path / "frontend").mkdir()

    with pytest.raises(StartupError, match="--skip-install"):
        build_startup_plan(
            root=tmp_path,
            bootstrap_python=Path("python3"),
            platform_name="linux",
            skip_install=True,
            skip_build=True,
            host="127.0.0.1",
            port=8000,
        )


def test_local_defaults_do_not_override_explicit_configuration() -> None:
    environment = {"DATABASE_URL": "sqlite:///chosen.db", "APP_ENV": "testing"}

    apply_local_defaults(environment)

    assert environment["DATABASE_URL"] == "sqlite:///chosen.db"
    assert environment["APP_ENV"] == "testing"
    assert environment["BATCH_WORKER_COUNT"] == "1"


@pytest.mark.parametrize("version", ["v23.9.0", "v25.0.0", "not-a-version"])
def test_node_version_rejects_unsupported_runtime(version: str) -> None:
    with pytest.raises(StartupError, match="Node.js 24"):
        validate_node_version(version)


def test_node_version_accepts_node_24() -> None:
    validate_node_version("v24.19.0")
