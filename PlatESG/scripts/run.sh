#!/bin/sh
set -eu
plat_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
repo_root=$(CDPATH= cd -- "$plat_root/.." && pwd)
export PYTHONPATH="$repo_root/ExtractESG/standards/src:$repo_root/ExtractESG/targeted_table_extract/backend/src${PYTHONPATH:+:$PYTHONPATH}"
export ESG_EXTENSION_ROOT="${ESG_EXTENSION_ROOT:-$repo_root/ExtractESG/.local/extensions}"
if [ ! -d "$plat_root/frontend/node_modules" ]; then
  npm --prefix "$plat_root/frontend" ci --no-audit --no-fund
fi
npm --prefix "$plat_root/frontend" run build
plat_python=${PLATESG_PYTHON:-}
if [ -z "$plat_python" ] && [ -x "$plat_root/.venv/bin/python" ]; then plat_python="$plat_root/.venv/bin/python"; fi
if [ -z "$plat_python" ] && [ -x "$repo_root/../../model/.runtime/venv/bin/python" ]; then plat_python="$repo_root/../../model/.runtime/venv/bin/python"; fi
if [ -z "$plat_python" ]; then plat_python=python3; fi
"$plat_python" "$repo_root/ExtractESG/scripts/bootstrap_extension_runtime.py" --root "$ESG_EXTENSION_ROOT"
mkdir -p "$plat_root/.local"
if [ ! -f "$plat_root/.local/review-token" ]; then
  (umask 077; "$plat_python" -c 'import secrets; print(secrets.token_urlsafe(32))' > "$plat_root/.local/review-token")
fi
PLATESG_REVIEW_TOKEN=${PLATESG_REVIEW_TOKEN:-$(cat "$plat_root/.local/review-token")}
export PLATESG_REVIEW_TOKEN
cd "$repo_root"
exec "$plat_python" -m uvicorn PlatESG.backend.main:app --host 127.0.0.1 --port "${PLATESG_PORT:-8765}" --workers 1
