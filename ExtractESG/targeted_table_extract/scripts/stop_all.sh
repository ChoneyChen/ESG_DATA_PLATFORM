#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
RUNTIME="$ROOT/.local/runtime"

for name in backend; do
  pid_file="$RUNTIME/$name.pid"
  if [[ -f "$pid_file" ]]; then
    pid="$(<"$pid_file")"
    if kill -0 "$pid" 2>/dev/null; then
      command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      expected="http.server"
      [[ "$name" == "backend" ]] && expected="uvicorn esg_targeted.api.main:app"
      if [[ "$command_line" == *"$expected"* ]]; then
        kill "$pid"
        print "$name 已停止（PID $pid）。"
      else
        print -u2 "$name PID 文件已陈旧，未停止不相关进程 $pid。"
      fi
    else
      print "$name 当前未运行。"
    fi
    rm -f "$pid_file"
  fi
done
