#!/usr/bin/env python3
"""Install the one owned desktop entry; archive its previous text in local control data."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import os
import shlex

ROOT=Path(__file__).resolve().parents[2]


def install(destination):
    destination=destination.expanduser().absolute()
    if destination.is_symlink():raise ValueError('Refusing to overwrite a desktop symlink')
    if destination.exists():
        backup=ROOT/'.local/data/control/launcher-backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        backup.mkdir(parents=True,exist_ok=True)
        (backup/destination.name).write_bytes(destination.read_bytes())
    text='''#!/bin/zsh
# ESG current desktop entry. No old route, database path, build logic or credential is embedded here.
set -uo pipefail
TASK_DEFAULT_ROOT=__ROOT__
TASK_ROOT="${ESG_PROJECT_ROOT:-$TASK_DEFAULT_ROOT}"
TASK_LAUNCHER="$TASK_ROOT/ExtractESG/scripts/start.sh"
if [[ ! -f "$TASK_LAUNCHER" ]]; then
  print -u2 "找不到新版工作台：$TASK_LAUNCHER"
  print -u2 '仓库移动后，请设置 ESG_PROJECT_ROOT 或重新运行 install_desktop_launcher.py。'
  [[ -t 0 ]] && { print '按回车键关闭…'; read -r _; }
  exit 1
fi
cd "$TASK_ROOT" || exit 1
zsh "$TASK_LAUNCHER" "$@"
TASK_RESULT=$?
if (( TASK_RESULT != 0 )) && [[ -t 0 ]]; then
  print -u2 '启动未完成；现有数据保留。日志在 ESG_DATA_ROOT/logs。按回车键关闭…'
  read -r _
fi
exit "$TASK_RESULT"
'''.replace('__ROOT__',shlex.quote(str(ROOT)))
    temporary=destination.with_suffix('.command.tmp')
    temporary.write_text(text);temporary.chmod(0o755);temporary.replace(destination)
    return destination


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--destination',type=Path,default=Path.home()/'Desktop/启动ESG统一工作台.command')
    args=parser.parse_args();print('已更新桌面入口：'+str(install(args.destination)))


if __name__=='__main__':main()
