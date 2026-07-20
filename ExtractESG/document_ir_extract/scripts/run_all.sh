#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_URL="${ESG_V2_BACKEND_URL:-http://127.0.0.1:18080}"
FRONTEND_URL="${ESG_V2_FRONTEND_URL:-http://127.0.0.1:18081}"
LOG_DIR="$ROOT/.local/logs"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

BACKEND_PID=""
FRONTEND_PID=""
OWNS_BACKEND=0
OWNS_FRONTEND=0

mkdir -p "$LOG_DIR"

backend_is_ready() {
  curl --silent --fail --max-time 2 "$BACKEND_URL/health" 2>/dev/null \
    | grep -q '"service":"esg-v2-backend"'
}

frontend_is_ready() {
  curl --silent --fail --max-time 2 "$FRONTEND_URL/" 2>/dev/null \
    | grep -q 'ESG Evidence Hub'
}

show_port_owner() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
}

stop_process_tree() {
  local pid="$1"
  local child

  [ -n "$pid" ] || return 0
  while IFS= read -r child; do
    [ -n "$child" ] && stop_process_tree "$child"
  done < <(pgrep -P "$pid" 2>/dev/null || true)
  kill -TERM "$pid" 2>/dev/null || true
}

cleanup() {
  trap - EXIT INT TERM HUP
  if [ "$OWNS_FRONTEND" -eq 1 ]; then
    stop_process_tree "$FRONTEND_PID"
  fi
  if [ "$OWNS_BACKEND" -eq 1 ]; then
    stop_process_tree "$BACKEND_PID"
  fi
  wait 2>/dev/null || true
}

handle_signal() {
  printf '\nStopping services started by this launcher...\n'
  cleanup
  exit 0
}

fail() {
  local message="$1"
  printf '\n[ERROR] %s\n' "$message"
  printf 'Backend log: %s\nFrontend log: %s\n' "$BACKEND_LOG" "$FRONTEND_LOG"
  printf '\nPress Enter to close...'
  read -r _ </dev/tty 2>/dev/null || true
  exit 1
}

wait_for_service() {
  local name="$1"
  local timeout="$2"
  local check_function="$3"
  local pid="$4"
  local log_file="$5"
  local elapsed=0

  while [ "$elapsed" -lt "$timeout" ]; do
    if "$check_function"; then
      printf '[OK] %s is ready.\n' "$name"
      return 0
    fi
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      printf '\n%s exited during startup. Recent log:\n' "$name"
      tail -40 "$log_file" 2>/dev/null || true
      return 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done

  printf '\n%s did not become ready within %s seconds. Recent log:\n' "$name" "$timeout"
  tail -40 "$log_file" 2>/dev/null || true
  return 1
}

trap cleanup EXIT
trap handle_signal INT TERM HUP

printf '\nESG Evidence Hub v2\n'
printf 'Project: %s\n\n' "$ROOT"

if backend_is_ready; then
  printf '[REUSE] Backend is already running at %s\n' "$BACKEND_URL"
elif lsof -tiTCP:18080 -sTCP:LISTEN >/dev/null 2>&1; then
  show_port_owner 18080
  fail "Port 18080 is occupied by another application."
else
  : >"$BACKEND_LOG"
  bash "$ROOT/scripts/run_backend.sh" >>"$BACKEND_LOG" 2>&1 &
  BACKEND_PID=$!
  OWNS_BACKEND=1
  printf '[START] Backend pid=%s\n' "$BACKEND_PID"
fi

if frontend_is_ready; then
  printf '[REUSE] Frontend is already running at %s\n' "$FRONTEND_URL"
elif lsof -tiTCP:18081 -sTCP:LISTEN >/dev/null 2>&1; then
  show_port_owner 18081
  fail "Port 18081 is occupied by another application."
else
  : >"$FRONTEND_LOG"
  bash "$ROOT/scripts/run_frontend.sh" >>"$FRONTEND_LOG" 2>&1 &
  FRONTEND_PID=$!
  OWNS_FRONTEND=1
  printf '[START] Frontend pid=%s\n' "$FRONTEND_PID"
fi

wait_for_service "Backend" 180 backend_is_ready "$BACKEND_PID" "$BACKEND_LOG" \
  || fail "Backend startup failed."
wait_for_service "Frontend" 30 frontend_is_ready "$FRONTEND_PID" "$FRONTEND_LOG" \
  || fail "Frontend startup failed."

printf '\nSystem is ready.\n'
printf 'Frontend: %s\n' "$FRONTEND_URL"
printf 'Backend:  %s\n' "$BACKEND_URL"
printf 'Logs:     %s\n' "$LOG_DIR"
printf '\nKeep this window open. Press Ctrl-C or close it to stop services started here.\n'

if [ "${ESG_V2_NO_BROWSER:-0}" != "1" ]; then
  open "$FRONTEND_URL" >/dev/null 2>&1 || true
fi

if [ "$OWNS_BACKEND" -eq 0 ] && [ "$OWNS_FRONTEND" -eq 0 ]; then
  printf 'Both services were already running; this launcher can now be closed.\n'
  exit 0
fi

while true; do
  sleep 2
  if [ "$OWNS_BACKEND" -eq 1 ] && ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    fail "Backend stopped unexpectedly."
  fi
  if [ "$OWNS_FRONTEND" -eq 1 ] && ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    fail "Frontend stopped unexpectedly."
  fi
done
