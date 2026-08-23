from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping, Sequence


class StartupError(RuntimeError):
    """A user-actionable local startup error."""


@dataclass(frozen=True)
class CommandStep:
    name: str
    args: tuple[str, ...]
    cwd: Path


def load_dotenv(path: Path, environ: MutableMapping[str, str]) -> set[str]:
    """Load simple KEY=VALUE entries without evaluating shell syntax."""
    loaded: set[str] = set()
    if not path.is_file():
        return loaded

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in environ:
            environ[key] = value
            loaded.add(key)
    return loaded


def apply_local_defaults(environ: MutableMapping[str, str]) -> None:
    environ.setdefault("APP_ENV", "development")
    environ.setdefault("DATABASE_URL", "sqlite:///data/safety_detect.db")
    environ.setdefault("UPLOAD_DIR", "data/uploads")
    environ.setdefault("BATCH_RETENTION_DAYS", "30")
    environ.setdefault("BATCH_WORKER_COUNT", "1")


def venv_python(root: Path, platform_name: str) -> Path:
    if platform_name == "win32":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def validate_node_version(version: str) -> None:
    match = re.fullmatch(r"v?(\d+)(?:\.\d+){1,2}", version.strip())
    if match is None or int(match.group(1)) != 24:
        raise StartupError(
            f"需要 Node.js 24.x，当前检测结果为 {version.strip() or '未知'}。"
        )


def _npm_command(platform_name: str) -> str:
    return "npm.cmd" if platform_name == "win32" else "npm"


def build_startup_plan(
    *,
    root: Path,
    bootstrap_python: Path,
    platform_name: str,
    skip_install: bool,
    skip_build: bool,
    host: str,
    port: int,
) -> list[CommandStep]:
    python = venv_python(root, platform_name)
    frontend = root / "frontend"
    node_modules = frontend / "node_modules"
    dist = frontend / "dist"
    missing_python = not python.is_file()
    missing_frontend = not node_modules.is_dir()

    if skip_install and (missing_python or missing_frontend):
        raise StartupError(
            "--skip-install 需要已有 .venv 和 frontend/node_modules；"
            "请去掉该参数完成首次安装。"
        )
    if skip_build and not dist.is_dir():
        raise StartupError(
            "--skip-build 需要已有 frontend/dist；请先执行一次完整启动。"
        )

    steps: list[CommandStep] = []
    if missing_python:
        steps.append(
            CommandStep(
                "create-venv",
                (str(bootstrap_python), "-m", "venv", str(root / ".venv")),
                root,
            )
        )
    if missing_python and not skip_install:
        steps.append(
            CommandStep(
                "install-python",
                (str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")),
                root,
            )
        )
    if missing_frontend and not skip_install:
        steps.append(
            CommandStep(
                "install-frontend",
                (_npm_command(platform_name), "ci"),
                frontend,
            )
        )
    if not skip_build:
        steps.append(
            CommandStep(
                "build-frontend",
                (_npm_command(platform_name), "run", "build"),
                frontend,
            )
        )
    steps.append(
        CommandStep(
            "start-server",
            (
                str(python),
                "-m",
                "uvicorn",
                "backend.main:app",
                "--host",
                host,
                "--port",
                str(port),
            ),
            root,
        )
    )
    return steps


def _check_prerequisites(needs_node: bool) -> None:
    if sys.version_info < (3, 12):
        raise StartupError(
            f"需要 Python 3.12 或更高版本，当前为 {sys.version.split()[0]}。"
        )
    if not needs_node:
        return
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if node is None or npm is None:
        raise StartupError("未找到 Node.js/npm，请先安装 Node.js 24.x。")
    completed = subprocess.run(
        [node, "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    validate_node_version(completed.stdout)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="准备依赖、构建前端并启动 Safety Detect 本地服务。"
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="跳过依赖安装；要求 .venv 和 frontend/node_modules 已存在。",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="跳过前端构建；要求 frontend/dist 已存在。",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址。")
    parser.add_argument("--port", type=int, default=8000, help="监听端口。")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise StartupError("端口必须在 1 到 65535 之间。")

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", os.environ)
    apply_local_defaults(os.environ)
    plan = build_startup_plan(
        root=root,
        bootstrap_python=Path(sys.executable),
        platform_name=sys.platform,
        skip_install=args.skip_install,
        skip_build=args.skip_build,
        host=args.host,
        port=args.port,
    )
    _check_prerequisites(any(step.name.startswith(("install-frontend", "build-frontend")) for step in plan))

    labels = {
        "create-venv": "创建 Python 虚拟环境",
        "install-python": "安装后端依赖",
        "install-frontend": "安装前端依赖",
        "build-frontend": "构建前端",
        "start-server": "启动本地服务",
    }
    for step in plan:
        print(f"[Safety Detect] {labels[step.name]}...", flush=True)
        if step.name == "start-server":
            print(f"[Safety Detect] 控制台：http://{args.host}:{args.port}/", flush=True)
            print("[Safety Detect] 按 Ctrl+C 停止服务。", flush=True)
        subprocess.run(step.args, cwd=step.cwd, env=os.environ.copy(), check=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (StartupError, subprocess.CalledProcessError) as exc:
        print(f"[Safety Detect] 启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
