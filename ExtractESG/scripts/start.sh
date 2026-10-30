#!/bin/zsh
# Current composition entry. Desktop, terminal and automation share this controller.
set -eu
script_dir="${0:A:h}"
extract_root="${script_dir:h}"
export ESG_PROJECT_ROOT="${ESG_PROJECT_ROOT:-${extract_root:h}}"
python="${ESG_DOCUMENT_PYTHON:-$extract_root/.local/python/bin/python}"
[[ -x "$python" ]] || { print -u2 '缺少本地Python环境，请配置ESG_DOCUMENT_PYTHON。'; exit 1; }
exec "$python" "$script_dir/workbench.py" "$@"
