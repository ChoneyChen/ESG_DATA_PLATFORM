#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/../.." && pwd)"
task_python="${ESG_DOCUMENT_PYTHON:-$task_root/ExtractESG/.local/python/bin/python}"
exec "$task_python" -m pip install --no-deps --no-build-isolation \
  -e "$task_root/ExtractESG/contracts" -e "$task_root/ExtractESG/shared_runtime" \
  -e "$task_root/ClauseESG" -e "$task_root/ExtractESG" -e "$task_root/ConsoleESG/backend" -e "$task_root/database"
