#!/bin/sh
set -eu
export PLATESG_AUTO_PUBLISH=0
plat_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
repo_root=$(CDPATH= cd -- "$plat_root/.." && pwd)
if [ ! -d "$plat_root/frontend/node_modules" ]; then
  npm --prefix "$plat_root/frontend" ci --no-audit --no-fund
fi
npm --prefix "$plat_root/frontend" run build
plat_python=${ESG_PYTHON:-}
if [ -z "$plat_python" ] && [ -x "$repo_root/ExtractESG/.local/python/bin/python" ]; then plat_python="$repo_root/ExtractESG/.local/python/bin/python"; fi
if [ -z "$plat_python" ]; then plat_python=python3; fi
cd "$repo_root"
exec "$plat_python" -m uvicorn PlatESG.backend.main:app --host 127.0.0.1 --port "${PLATESG_PORT:-8765}" --workers 1
