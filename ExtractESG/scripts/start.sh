#!/bin/zsh
set -u
export PYTHONPATH="${0:A:h:h}/contracts/src:${0:A:h:h}/shared_runtime/src:${0:A:h:h}/document/src:${0:A:h:h}/pipeline/src:${0:A:h:h}/extraction/src:${0:A:h:h}/../ConsoleESG/backend/src:${0:A:h:h}/../ClauseESG/src:${0:A:h:h}/../database/src"

SCRIPT_DIR="${0:A:h}"
EXTRACTESG_ROOT="${SCRIPT_DIR:h}"
WORKSPACE_ROOT="$(cd "$EXTRACTESG_ROOT/../.." && pwd)"


DATA_ROOT="${ESG_DATA_ROOT:-$EXTRACTESG_ROOT/../.local/data}"
RUNTIME_ROOT="${ESG_PLATFORM_RUNTIME_ROOT:-$DATA_ROOT/control}"
LOG_ROOT="$DATA_ROOT/logs"

DOC_BACKEND_URL="${ESG_DOCUMENT_BACKEND_URL:-http://127.0.0.1:18080}"
PLATFORM_FRONTEND_URL="${ESG_PLATFORM_FRONTEND_URL:-http://127.0.0.1:18081}"
TARGET_BACKEND_URL="${ESG_EXTRACTION_BACKEND_URL:-http://127.0.0.1:18180}"
DATABASE_URL="${ESG_DATABASE_INGEST_URL:-http://127.0.0.1:18280}"
export ESG_DATABASE_INGEST_URL="$DATABASE_URL"

typeset -A PIDS
typeset -A OWNED
typeset -A LOGS
SERVICE_ORDER=(database_receiver extraction_backend doc_backend platform_frontend)

mkdir -p "$RUNTIME_ROOT"
mkdir -p "$LOG_ROOT"

LOGS[doc_backend]="$LOG_ROOT/document.log"
LOGS[platform_frontend]="$LOG_ROOT/console.log"
LOGS[extraction_backend]="$LOG_ROOT/extraction.log"
LOGS[database_receiver]="$LOG_ROOT/database.log"
RELEASE_PYTHON="${ESG_RELEASE_PYTHON:-$EXTRACTESG_ROOT/.local/python/bin/python}"
[[ -x "$RELEASE_PYTHON" ]] || RELEASE_PYTHON="${ESG_MODEL_PYTHON:-$HOME/Desktop/model/.runtime/venv/bin/python}"
[[ -x "$RELEASE_PYTHON" ]] || RELEASE_PYTHON="$(command -v python3)"
typeset -A READINESS_ERRORS

service_ready() {
  local url identity diagnostic
  if [[ "$1" == "database_receiver" ]]; then
    READINESS_ERRORS[$1]="数据库接收器或封存合同尚未就绪"
    "$RELEASE_PYTHON" -c 'import json,sys,urllib.request; data=json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(sys.argv[1]+"/health",timeout=5)); assert data["status"]=="ok" and data["export_contract"]=="extraction-export-1.0" and not data.get("restart_required",False)' "$DATABASE_URL" >/dev/null 2>&1
    return $?
  fi
  case "$1" in
    doc_backend)
      url="$DOC_BACKEND_URL/health/compatibility"; identity="document-backend"
      ;;
    platform_frontend)
      url="$PLATFORM_FRONTEND_URL/api/compatibility"; identity="workbench-frontend"
      ;;
    extraction_backend)
      url="$TARGET_BACKEND_URL/api/compatibility"; identity="extraction-backend"
      ;;
  esac
  diagnostic="$("$RELEASE_PYTHON" -m esg_runtime.release --root "$EXTRACTESG_ROOT" --url "$url" --service "$identity" 2>&1)"
  local result=$?
  READINESS_ERRORS[$1]="$diagnostic"
  return $result
}

service_port() {
  case "$1" in
    doc_backend) print 18080 ;;
    platform_frontend) print 18081 ;;
    extraction_backend) print 18180 ;;
    database_receiver) print 18280 ;;
  esac
}

service_label() {
  case "$1" in
    doc_backend) print "文档证据服务" ;;
    platform_frontend) print "ESG 统一前端" ;;
    extraction_backend) print "条款提取服务" ;;
    database_receiver) print "独立原值数据库接收器" ;;
  esac
}

stop_process_tree() {
  local pid="$1" child
  [[ -n "$pid" ]] || return 0
  while IFS= read -r child; do
    [[ -n "$child" ]] && stop_process_tree "$child"
  done < <(pgrep -P "$pid" 2>/dev/null || true)
  kill -TERM "$pid" 2>/dev/null || true
}

cleanup() {
  trap - EXIT INT TERM HUP
  local service
  for service in ${(Oa)SERVICE_ORDER}; do
    if [[ "${OWNED[$service]:-0}" == "1" ]]; then
      stop_process_tree "${PIDS[$service]}"
    fi
  done
  wait 2>/dev/null || true
}

fail() {
  local message="$1"
  print -u2 "\n[失败] $message"
  print -u2 "日志目录：$LOG_ROOT"
  cleanup
  if [[ "${ESG_PLATFORM_NONINTERACTIVE:-0}" != "1" ]]; then
    print "\n按回车键关闭窗口…"
    read -r _ </dev/tty 2>/dev/null || true
  fi
  exit 1
}

is_our_service() {
  local pid="$1" command
  [[ -n "$pid" ]] || return 1
  command="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  [[ -n "$command" ]] || return 1
  # The workspace path is the surest identity; fall back to the three
  # entrypoints in case a process was started through a relative path.
  [[ "$command" == *"$WORKSPACE_ROOT"* ]] && return 0
  [[ "$command" == *"esg_document.api.main"* || "$command" == *"esg_console.application"* || "$command" == *"esg_database.api"* || "$command" == *"esg_extraction.pipeline.api"* \
    || "$command" == *"serve_workbench_frontend.py"* ]]
}

wait_for_port_free() {
  local port="$1" timeout="$2" elapsed=0
  while (( elapsed < timeout )); do
    /usr/sbin/lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || return 0
    sleep 1
    (( elapsed += 1 ))
  done
  return 1
}

start_service() {
  local service="$1" port label
  port="$(service_port "$service")"
  label="$(service_label "$service")"
  if service_ready "$service"; then
    OWNED[$service]=0
    print "[复用] $label 已经运行。"
    return 0
  fi
  if /usr/sbin/lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    local listener
    listener="$(/usr/sbin/lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1)"
    # A service started before the last source update fails readiness by
    # design. It is still this workspace's own process, so the launcher can
    # replace it; anything else on the port must be stopped by the operator.
    if is_our_service "$listener" && [[ "${ESG_PLATFORM_RESTART_STALE:-1}" != "0" ]]; then
      print "[更新] $label 是更新前的旧构建，正在停止并重建（${READINESS_ERRORS[$service]}）。"
      stop_process_tree "$listener"
      wait_for_port_free "$port" 20 || fail "$label 停止后端口 $port 仍被占用。"
    else
      /usr/sbin/lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
      fail "$label 无法启动：端口 $port 的服务未通过版本与业务检查。${READINESS_ERRORS[$service]}。请先关闭原启动窗口或停止旧服务，再重新启动；已有任务与成果保留。"
    fi
  fi

  : >"${LOGS[$service]}"
  case "$service" in
    doc_backend) bash "$SCRIPT_DIR/run_service.sh" document >>"${LOGS[$service]}" 2>&1 & ;;
    platform_frontend) bash "$SCRIPT_DIR/run_service.sh" console >>"${LOGS[$service]}" 2>&1 & ;;
    extraction_backend) bash "$SCRIPT_DIR/run_service.sh" extraction >>"${LOGS[$service]}" 2>&1 & ;;
    database_receiver) bash "$SCRIPT_DIR/run_service.sh" database >>"${LOGS[$service]}" 2>&1 & ;;
  esac
  PIDS[$service]=${!}
  OWNED[$service]=1
  print "[启动] $label · PID ${PIDS[$service]}"
}

wait_for_service() {
  local service="$1" timeout="$2" elapsed=0 label
  label="$(service_label "$service")"
  while (( elapsed < timeout )); do
    if service_ready "$service"; then
      print "[就绪] $label"
      return 0
    fi
    if [[ "${OWNED[$service]:-0}" == "1" ]] \
      && ! kill -0 "${PIDS[$service]}" 2>/dev/null; then
      print -u2 "\n$label 启动时退出，最近日志："
      tail -50 "${LOGS[$service]}" 2>/dev/null || true
      return 1
    fi
    sleep 1
    (( elapsed += 1 ))
  done
  print -u2 "\n等待 $label 超时，最近日志："
  print -u2 "${READINESS_ERRORS[$service]:-版本检查未完成}"
  tail -50 "${LOGS[$service]}" 2>/dev/null || true
  return 1
}

# Compatibility endpoints can answer before the first report-library scan is
# warm. Opening the browser at that moment makes the console show a transient
# 503 while the PDF resource index is warming. Read the current resource API.
wait_for_report_library() {
  local timeout="$1" elapsed=0
  while (( elapsed < timeout )); do
    if "${RELEASE_PYTHON}" -c 'import json,sys,urllib.request; json.load(urllib.request.urlopen(sys.argv[1], timeout=10))' \
      "${PLATFORM_FRONTEND_URL%/}/document/api/report-resources" >/dev/null 2>&1; then
      print "[就绪] 报告库首次读取"
      return 0
    fi
    sleep 1
    (( elapsed += 1 ))
  done
  return 1
}

[[ -d "$EXTRACTESG_ROOT/document/src" ]] || fail "文档模块缺失"
[[ -d "$EXTRACTESG_ROOT/extraction/src" ]] || fail "提取模块缺失"

# Build the independent operator app before any service captures the release.
CONSOLE_ROOT="$EXTRACTESG_ROOT/../ConsoleESG/frontend"
[[ -f "$CONSOLE_ROOT/package.json" ]] || fail "找不到独立工作台 ConsoleESG"
command -v npm >/dev/null 2>&1 || fail "独立工作台需要 Node.js/npm"
if [[ ! -d "$CONSOLE_ROOT/node_modules" ]]; then
  [[ "${ESG_SKIP_DEPENDENCY_INSTALL:-0}" != "1" ]] || fail "ConsoleESG 依赖尚未安装，请先在该目录运行 npm ci"
  npm --prefix "$CONSOLE_ROOT" ci --no-audit --no-fund || fail "工作台依赖安装失败"
fi
npm --prefix "$CONSOLE_ROOT" run build || fail "独立工作台构建失败；既有服务和业务成果保留"

trap cleanup EXIT
trap 'print "\n正在停止本启动器创建的服务…"; cleanup; exit 0' INT TERM HUP

print "\nConsoleESG 数据生产工作台"
print "文档模块：$EXTRACTESG_ROOT/document"
print "提取模块：$EXTRACTESG_ROOT/extraction"
print "读取链路：  文字层直读（版面区域 + 原生文字 + 表格重建）；仅文字层不可用的页走本机视觉识别"
print "提取链路：  独立条款词典检索 → 结构定列＋本地语义选行 → 两类原值事实\n"

for service in $SERVICE_ORDER; do
  start_service "$service"
done

wait_for_service database_receiver 180 || fail "独立数据库接收器启动失败。"
wait_for_service doc_backend 180 || fail "文档证据服务启动失败。"
wait_for_service extraction_backend 120 || fail "条款提取服务启动失败。"
wait_for_service platform_frontend 30 || fail "ESG 统一前端启动失败。"
if ! wait_for_report_library 90; then
  print -u2 "[警告] 报告库首次读取尚未就绪，页面会自动重试；如持续失败请查看 $RUNTIME_ROOT/document-backend.log"
fi

print "\n四个数据生产服务均已就绪："
print "统一前端：          $PLATFORM_FRONTEND_URL"
print "文档证据服务：$DOC_BACKEND_URL"
print "条款提取服务：    $TARGET_BACKEND_URL"
print "独立数据库接收：    $DATABASE_URL"
print "日志：              $LOG_ROOT"

if [[ "${ESG_SKIP_BROWSER:-0}" != "1" ]]; then
  # `open` may only focus an already-open Safari tab when the URL is unchanged,
  # leaving an old DOM and cached ES modules alive after an upgrade.  A launch
  # token forces a real navigation. Operator credentials stay in page memory.
  launch_token="$(date -u +%Y%m%dT%H%M%SZ)"
  open "${PLATFORM_FRONTEND_URL%/}/?launch=${launch_token}" >/dev/null 2>&1 || true
fi

if [[ "${ESG_PLATFORM_EXIT_AFTER_READY:-0}" == "1" ]]; then
  print "启动检查完成，正在停止本次检查创建的服务。"
  exit 0
fi

owned_count=0
for service in $SERVICE_ORDER; do
  [[ "${OWNED[$service]:-0}" == "1" ]] && (( owned_count += 1 ))
done
if (( owned_count == 0 )); then
  print "三个服务均为复用状态；窗口可以直接关闭。"
  exit 0
fi

print "\n保持此窗口打开。关闭窗口或按 Ctrl-C 会停止本窗口启动的服务。"
while true; do
  sleep 2
  for service in $SERVICE_ORDER; do
    if [[ "${OWNED[$service]:-0}" == "1" ]] \
      && ! kill -0 "${PIDS[$service]}" 2>/dev/null; then
      fail "$(service_label "$service") 意外停止。"
    fi
  done
done
