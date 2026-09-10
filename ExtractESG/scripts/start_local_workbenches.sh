#!/bin/zsh
set -u

SCRIPT_DIR="${0:A:h}"
EXTRACTESG_ROOT="${SCRIPT_DIR:h}"
WORKSPACE_ROOT="$(cd "$EXTRACTESG_ROOT/../.." && pwd)"
DOCUMENT_ROOT="${ESG_DOCUMENT_IR_APP_ROOT:-$EXTRACTESG_ROOT/document_ir_extract}"
TARGETED_ROOT="${ESG_TARGETED_APP_ROOT:-$EXTRACTESG_ROOT/targeted_table_extract}"
RUNTIME_ROOT="${ESG_PLATFORM_RUNTIME_ROOT:-$WORKSPACE_ROOT/.local/platform-runtime}"

DOC_BACKEND_URL="${ESG_DOCUMENT_BACKEND_URL:-http://127.0.0.1:18080}"
PLATFORM_FRONTEND_URL="${ESG_PLATFORM_FRONTEND_URL:-http://127.0.0.1:18081}"
TARGET_BACKEND_URL="${ESG_TARGETED_BACKEND_URL:-http://127.0.0.1:18180}"

typeset -A PIDS
typeset -A OWNED
typeset -A LOGS
SERVICE_ORDER=(targeted_backend doc_backend platform_frontend)

mkdir -p "$RUNTIME_ROOT"

LOGS[doc_backend]="$RUNTIME_ROOT/document-backend.log"
LOGS[platform_frontend]="$RUNTIME_ROOT/platform-frontend.log"
LOGS[targeted_backend]="$RUNTIME_ROOT/targeted-backend.log"

service_ready() {
  case "$1" in
    doc_backend)
      curl --silent --fail --max-time 2 "$DOC_BACKEND_URL/health" 2>/dev/null \
        | grep -q '"service":"esg-v2-backend"'
      ;;
    platform_frontend)
      curl --silent --fail --max-time 2 "$PLATFORM_FRONTEND_URL/" 2>/dev/null \
        | grep -q 'Pipeline Workbench'
      ;;
    targeted_backend)
      curl --silent --fail --max-time 2 "$TARGET_BACKEND_URL/api/health" 2>/dev/null \
        | grep -q '"service":"targeted-table-extraction"' \
        && curl --silent --fail --max-time 2 "$TARGET_BACKEND_URL/api/config" 2>/dev/null \
        | grep -q '"semantic_providers"'
      ;;
  esac
}

service_port() {
  case "$1" in
    doc_backend) print 18080 ;;
    platform_frontend) print 18081 ;;
    targeted_backend) print 18180 ;;
  esac
}

service_label() {
  case "$1" in
    doc_backend) print "Document IR 后端" ;;
    platform_frontend) print "ESG 统一前端" ;;
    targeted_backend) print "抽取填表后端" ;;
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
  print -u2 "日志目录：$RUNTIME_ROOT"
  cleanup
  if [[ "${ESG_PLATFORM_NONINTERACTIVE:-0}" != "1" ]]; then
    print "\n按回车键关闭窗口…"
    read -r _ </dev/tty 2>/dev/null || true
  fi
  exit 1
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
    /usr/sbin/lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
    fail "$label 无法启动：端口 $port 被其他应用占用。"
  fi

  : >"${LOGS[$service]}"
  case "$service" in
    doc_backend) bash "$DOCUMENT_ROOT/scripts/run_backend.sh" >>"${LOGS[$service]}" 2>&1 & ;;
    platform_frontend) bash "$DOCUMENT_ROOT/scripts/run_frontend.sh" >>"${LOGS[$service]}" 2>&1 & ;;
    targeted_backend) zsh "$TARGETED_ROOT/scripts/run_backend.sh" >>"${LOGS[$service]}" 2>&1 & ;;
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
  tail -50 "${LOGS[$service]}" 2>/dev/null || true
  return 1
}

[[ -d "$DOCUMENT_ROOT" ]] || fail "找不到 Document IR 应用：$DOCUMENT_ROOT"
[[ -d "$TARGETED_ROOT" ]] || fail "找不到抽取填表应用：$TARGETED_ROOT"

trap cleanup EXIT
trap 'print "\n正在停止本启动器创建的服务…"; cleanup; exit 0' INT TERM HUP

print "\nESG Evidence Hub 统一工作台"
print "Document IR：$DOCUMENT_ROOT"
print "抽取填表：  $TARGETED_ROOT\n"

for service in $SERVICE_ORDER; do
  start_service "$service"
done

wait_for_service doc_backend 180 || fail "Document IR 后端启动失败。"
wait_for_service targeted_backend 120 || fail "抽取填表后端启动失败。"
wait_for_service platform_frontend 30 || fail "ESG 统一前端启动失败。"

print "\n三个服务均已就绪："
print "统一前端：          $PLATFORM_FRONTEND_URL"
print "Document IR 后端：$DOC_BACKEND_URL"
print "抽取填表后端：    $TARGET_BACKEND_URL"
print "日志：              $RUNTIME_ROOT"

if [[ "${ESG_PLATFORM_NO_BROWSER:-0}" != "1" ]]; then
  open "$PLATFORM_FRONTEND_URL" >/dev/null 2>&1 || true
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
