#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT_DIR/scripts/start_local.py" "$@"
fi

echo "[Safety Detect] 启动失败：未找到 Python 3.12+。" >&2
exit 1
