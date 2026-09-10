#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
PYTHON="${ESG_MODEL_PYTHON:-$HOME/Desktop/model/.runtime/venv/bin/python}"
HOST="${ESG_TARGETED_BACKEND_HOST:-127.0.0.1}"
PORT="${ESG_TARGETED_BACKEND_PORT:-18180}"

[[ -f "$ROOT/.env" ]] && set -a && source "$ROOT/.env" && set +a
if [[ ! -x "$PYTHON" ]]; then
  print -u2 "找不到 Python：$PYTHON；请先运行 scripts/setup.sh。"
  exit 1
fi

cd "$ROOT/backend"
exec "$PYTHON" -m uvicorn esg_targeted.api.main:app --host "$HOST" --port "$PORT"
