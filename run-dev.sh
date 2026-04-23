#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${AF_HOST:-127.0.0.1}"
PORT="${AF_PORT:-8000}"
CMD="${1:-start}"
RUN_DIR="$ROOT_DIR/.run"
PID_FILE="$RUN_DIR/asset-factory-dev.pid"
LOG_FILE="$RUN_DIR/asset-factory-dev.log"

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -z "${pid:-}" ]]; then
    return 1
  fi
  kill -0 "$pid" 2>/dev/null
}

start_server() {
  mkdir -p "$RUN_DIR"
  if is_running; then
    echo "[dev] already running (pid=$(cat "$PID_FILE"))"
    echo "[dev] app=http://$HOST:$PORT/app/"
    return 0
  fi

  echo "[dev] starting Asset Factory (frontend+backend)"
  echo "[dev] host=$HOST port=$PORT"
  echo "[dev] data=${ASSET_FACTORY_DATA_DIR:-$ROOT_DIR/data}"
  echo "[dev] db=${ASSET_FACTORY_DB_PATH:-${ASSET_FACTORY_DATA_DIR:-$ROOT_DIR/data}/asset-factory.db}"
  echo "[dev] app=http://$HOST:$PORT/app/"
  echo "[dev] log=$LOG_FILE"

  nohup "$ROOT_DIR/.venv/bin/uvicorn" server:app --host "$HOST" --port "$PORT" --reload \
    >"$LOG_FILE" 2>&1 < /dev/null &
  echo $! > "$PID_FILE"
  echo "[dev] started pid=$(cat "$PID_FILE")"
}

stop_server() {
  if ! is_running; then
    rm -f "$PID_FILE"
    echo "[dev] not running"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  echo "[dev] stopping pid=$pid"
  kill "$pid" 2>/dev/null || true
  sleep 0.5
  if kill -0 "$pid" 2>/dev/null; then
    echo "[dev] force killing pid=$pid"
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  echo "[dev] stopped"
}

status_server() {
  if is_running; then
    echo "[dev] running pid=$(cat "$PID_FILE")"
    echo "[dev] app=http://$HOST:$PORT/app/"
    echo "[dev] log=$LOG_FILE"
  else
    echo "[dev] stopped"
  fi
}

case "$CMD" in
  start)
    start_server
    ;;
  stop)
    stop_server
    ;;
  restart)
    stop_server
    start_server
    ;;
  status)
    status_server
    ;;
  *)
    echo "Usage: ./run-dev.sh [start|stop|restart|status]"
    exit 1
    ;;
esac
