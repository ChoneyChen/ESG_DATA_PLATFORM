#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
RUNTIME="$ROOT/.local/runtime"
BACKEND_PORT="${ESG_TARGETED_BACKEND_PORT:-18180}"
mkdir -p "$RUNTIME"

[[ -f "$ROOT/.env" ]] && set -a && source "$ROOT/.env" && set +a

port_in_use() {
  /usr/sbin/lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

start_service() {
  local name="$1" port="$2" script="$3"
  local pid_file="$RUNTIME/$name.pid" log_file="$RUNTIME/$name.log"
  if [[ -f "$pid_file" ]] && kill -0 "$(<"$pid_file")" 2>/dev/null; then
    print "$name 已在运行（PID $(<"$pid_file")）。"
    return
  fi
  if port_in_use "$port"; then
    print -u2 "$name 无法启动：端口 $port 已被其他进程占用。"
    exit 1
  fi
  nohup "$script" </dev/null >"$log_file" 2>&1 &
  local service_pid=$!
  print "$service_pid" >"$pid_file"
  disown "$service_pid" 2>/dev/null || true
  print "$name 已启动（PID $service_pid，日志 $log_file）。"
}

start_service "backend" "$BACKEND_PORT" "$ROOT/scripts/run_backend.sh"

for _ in {1..40}; do
  if curl -fsS "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1; then
    print "定向抽取后端已就绪。统一工作台由 ExtractESG/scripts/start_local_workbenches.sh 启动。"
    print "后端 API 文档：http://127.0.0.1:$BACKEND_PORT/api/docs"
    exit 0
  fi
  sleep 0.25
done

print -u2 "服务未在预期时间内就绪，请查看 $RUNTIME/backend.log。"
exit 1
