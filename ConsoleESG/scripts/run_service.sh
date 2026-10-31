#!/bin/bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EXTRACT_ROOT="$TASK_ROOT/ExtractESG"
unset PYTHONPATH
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
case "${1:-}" in
 document) PYTHON="${ESG_DOCUMENT_PYTHON:-$EXTRACT_ROOT/.local/python/bin/python}"; APP="esg_console.application:app"; PORT=18080 ;;
 extraction) PYTHON="${ESG_MODEL_PYTHON:-$HOME/Desktop/model/.runtime/venv/bin/python}"; APP="esg_extraction.pipeline.api:app"; PORT=18180 ;;
 database)
   PYTHON="${ESG_DOCUMENT_PYTHON:-$EXTRACT_ROOT/.local/python/bin/python}"; APP="esg_database.api:app"; PORT=18280
   export ESG_DATABASE_DATA_DIR="${ESG_DATABASE_DATA_DIR:-${ESG_DATA_ROOT:-$EXTRACT_ROOT/../.local/data}/databases/capture}"
   if [[ ! -d "$ESG_DATABASE_DATA_DIR" ]]; then
     echo 'Capture database missing. Initialize a new isolated store explicitly with database/runtime/manage.mjs init.' >&2
     exit 1
   fi
   ;;
 plat) export PYTHONPATH="$TASK_ROOT"; PYTHON="${ESG_DOCUMENT_PYTHON:-$EXTRACT_ROOT/.local/python/bin/python}"; APP="PlatESG.backend.main:app"; PORT=8765 ;;
 console) exec "${ESG_FRONTEND_PYTHON:-$EXTRACT_ROOT/.local/python/bin/python}" "$TASK_ROOT/ConsoleESG/scripts/serve_workbench_frontend.py" ;;
 *) echo 'Usage: run_service.sh document|extraction|console|database|plat' >&2; exit 2 ;;
esac
if [[ ! -x "$PYTHON" ]]; then echo 'Python runtime missing; install ExtractESG/pyproject.toml into the configured local runtime.' >&2; exit 1; fi
exec "$PYTHON" -m uvicorn "$APP" --host 127.0.0.1 --port "$PORT"
