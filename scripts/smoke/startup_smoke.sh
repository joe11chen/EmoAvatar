#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PORT="${PORT:-6006}"
READY_PATH="${READY_PATH:-/webrtcapi.html}"
STARTUP_TIMEOUT_SEC="${STARTUP_TIMEOUT_SEC:-240}"
POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-2}"
LOG_DIR="${LOG_DIR:-tmp/smoke}"

mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_FILE:-$LOG_DIR/startup_smoke_$(date -u +%Y%m%dT%H%M%SZ).log}"

if [[ $# -gt 0 ]]; then
  APP_CMD=("$@")
else
  APP_CMD=(
    python app.py
    --transport webrtc
    --renderer musetalk
    --multi_avatar True
    --tts indextts2
    --listenport "$PORT"
  )
fi

APP_PID=""

cleanup() {
  if [[ -n "$APP_PID" ]] && kill -0 "$APP_PID" >/dev/null 2>&1; then
    kill "$APP_PID" >/dev/null 2>&1 || true
    sleep 1
    if kill -0 "$APP_PID" >/dev/null 2>&1; then
      kill -9 "$APP_PID" >/dev/null 2>&1 || true
    fi
  fi
}
trap cleanup EXIT INT TERM

print_log_tail() {
  if [[ -f "$LOG_FILE" ]]; then
    echo "[smoke] ---- log tail ----"
    tail -n 40 "$LOG_FILE" || true
    echo "[smoke] ------------------"
  fi
}

echo "[smoke] log file: $LOG_FILE"
echo "[smoke] command: ${APP_CMD[*]}"

"${APP_CMD[@]}" >"$LOG_FILE" 2>&1 &
APP_PID="$!"

deadline=$((SECONDS + STARTUP_TIMEOUT_SEC))
ready=0

while (( SECONDS < deadline )); do
  if ! kill -0 "$APP_PID" >/dev/null 2>&1; then
    echo "[smoke] FAIL: process exited early. See $LOG_FILE"
    print_log_tail
    exit 1
  fi

  if curl -fsS "http://127.0.0.1:${PORT}${READY_PATH}" >/dev/null 2>&1; then
    ready=1
    break
  fi

  sleep "$POLL_INTERVAL_SEC"
done

if [[ "$ready" -ne 1 ]]; then
  echo "[smoke] FAIL: startup timeout (${STARTUP_TIMEOUT_SEC}s). See $LOG_FILE"
  print_log_tail
  exit 1
fi

if rg -n "Traceback \\(most recent call last\\)|ModuleNotFoundError|ImportError|RuntimeError" "$LOG_FILE" >/dev/null 2>&1; then
  echo "[smoke] FAIL: startup log contains fatal error patterns. See $LOG_FILE"
  print_log_tail
  exit 1
fi

echo "[smoke] PASS: service started and readiness endpoint is reachable."
echo "[smoke] PASS: no fatal startup error pattern found."
