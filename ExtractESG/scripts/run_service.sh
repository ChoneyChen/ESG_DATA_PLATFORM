#!/bin/bash
set -euo pipefail
EXTRACT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$EXTRACT_ROOT/contracts/src:$EXTRACT_ROOT/shared_runtime/src:$EXTRACT_ROOT/document/src:$EXTRACT_ROOT/pipeline/src:$EXTRACT_ROOT/extraction/src"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
case "${1:-}" in
 document) PYTHON="${ESG_DOCUMENT_PYTHON:-$EXTRACT_ROOT/.local/python/bin/python}"; APP="esg_document.api.main:app"; PORT=18080 ;;
 extraction) PYTHON="${ESG_MODEL_PYTHON:-$HOME/Desktop/model/.runtime/venv/bin/python}"; APP="esg_extraction.pipeline.api:app"; PORT=18180 ;;
 console) exec "${ESG_FRONTEND_PYTHON:-$EXTRACT_ROOT/.local/python/bin/python}" "$EXTRACT_ROOT/scripts/serve_workbench_frontend.py" ;;
 *) echo 'Usage: run_service.sh document|extraction|console' >&2; exit 2 ;;
esac
if [[ ! -x "$PYTHON" ]]; then echo 'Python runtime missing; install ExtractESG/pyproject.toml into the configured local runtime.' >&2; exit 1; fi
exec "$PYTHON" -m uvicorn "$APP" --host 127.0.0.1 --port "$PORT"
