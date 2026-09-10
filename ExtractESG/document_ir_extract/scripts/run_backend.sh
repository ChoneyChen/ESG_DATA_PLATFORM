#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend"

for ENV_FILE in "$ROOT/.env" "$ROOT/.env.local"; do
  if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
done

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ] && command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3.12)"
fi
if [ -z "$PYTHON_BIN" ] && [ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]; then
  PYTHON_BIN="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
fi
if [ -z "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
if [ "${ESG_SKIP_DEPENDENCY_INSTALL:-0}" != "1" ]; then
  python -m pip install -U pip
  python -m pip install -e .
fi

export PYTHONPATH="$ROOT/backend/src"
UVICORN_ARGS=(esg_v2.api.main:app --host 127.0.0.1 --port 18080)
if [ "${ESG_V2_DEV_RELOAD:-0}" = "1" ]; then
  UVICORN_ARGS+=(--reload)
fi
exec uvicorn "${UVICORN_ARGS[@]}"
