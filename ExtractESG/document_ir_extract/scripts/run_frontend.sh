#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ESG_FRONTEND_PYTHON:-}"
if [ -z "$PYTHON_BIN" ] && [ -x "$ROOT/backend/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT/backend/.venv/bin/python"
fi
if [ -z "$PYTHON_BIN" ] && command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.12)"
fi
if [ -z "$PYTHON_BIN" ]; then
  printf '找不到可用的 Python 3.12；请先运行 Document IR 后端安装脚本。\n' >&2
  exit 1
fi

exec "$PYTHON_BIN" -m http.server 18081 --bind 127.0.0.1 --directory "$ROOT/frontend"
