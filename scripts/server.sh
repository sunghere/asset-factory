#!/usr/bin/env bash
# Asset Factory 서버 관리 스크립트.
#
# FastAPI + uvicorn 한 프로세스로 백엔드 API와 프론트엔드(/static, /cherry-pick)를
# 모두 서빙하므로 별도 프론트 프로세스는 필요 없다.
#
# 사용:
#   ./scripts/server.sh start          # 백그라운드 실행 (PID/로그 파일 기록)
#   ./scripts/server.sh stop           # 실행 중인 프로세스 종료
#   ./scripts/server.sh restart        # stop + start
#   ./scripts/server.sh status         # 실행 상태 + 최근 로그 20줄
#   ./scripts/server.sh logs           # 로그 tail -f
#   ./scripts/server.sh dev            # foreground + --reload (개발용)
#
# 환경변수 (모두 선택):
#   AF_PORT=47823                      # 기본 포트 (잘 안 쓰는 5-digit ephemeral)
#   AF_HOST=0.0.0.0
#   AF_LOG_LEVEL=info
#   AF_VENV=.venv                      # uvicorn 위치
#   AF_PID_FILE=data/server.pid
#   AF_LOG_FILE=data/server.log
#
# 그 외 SD_HOST / API_KEY / MIN_FREE_DISK_MB 등 server.py가 읽는 환경변수는
# 프로젝트 루트의 `.env` 파일을 자동 로드한다 (server.py가 python-dotenv 사용).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

AF_PORT="${AF_PORT:-47823}"
AF_HOST="${AF_HOST:-0.0.0.0}"
AF_LOG_LEVEL="${AF_LOG_LEVEL:-info}"
AF_VENV="${AF_VENV:-.venv}"
AF_PID_FILE="${AF_PID_FILE:-data/server.pid}"
AF_LOG_FILE="${AF_LOG_FILE:-data/server.log}"

UVICORN="$ROOT_DIR/$AF_VENV/bin/uvicorn"
if [[ ! -x "$UVICORN" ]]; then
  if command -v uvicorn >/dev/null 2>&1; then
    UVICORN="$(command -v uvicorn)"
  else
    echo "[server] uvicorn 을 찾을 수 없음. .venv 활성화 또는 'pip install -r requirements.txt' 필요." >&2
    exit 1
  fi
fi

mkdir -p "$(dirname "$AF_PID_FILE")" "$(dirname "$AF_LOG_FILE")"

is_running() {
  if [[ ! -f "$AF_PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$AF_PID_FILE" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  if kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  return 1
}

current_pid() {
  cat "$AF_PID_FILE" 2>/dev/null || echo ""
}

# 같은 포트를 점유한 외부 프로세스가 있는지 (다른 도구로 띄운 경우) 안내.
warn_port_busy() {
  if command -v lsof >/dev/null 2>&1; then
    local owner
    owner="$(lsof -nP -iTCP:"$AF_PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR==2{print $2" ("$1")"}')"
    if [[ -n "$owner" ]]; then
      echo "[server] 경고: 포트 $AF_PORT 는 이미 사용 중 (pid $owner). AF_PORT 환경변수로 다른 포트를 지정하세요." >&2
      return 1
    fi
  fi
  return 0
}

cmd_start() {
  if is_running; then
    echo "[server] 이미 실행 중 (pid $(current_pid), port $AF_PORT)"
    return 0
  fi
  if ! warn_port_busy; then
    return 1
  fi
  echo "[server] 시작 — host=$AF_HOST port=$AF_PORT log=$AF_LOG_FILE"
  # nohup + setsid 로 부모 셸과 분리
  nohup "$UVICORN" server:app \
    --host "$AF_HOST" --port "$AF_PORT" \
    --log-level "$AF_LOG_LEVEL" \
    >>"$AF_LOG_FILE" 2>&1 &
  local pid=$!
  echo "$pid" >"$AF_PID_FILE"
  # 짧게 대기 후 살아있는지 확인
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "[server] 시작 실패. 최근 로그:" >&2
    tail -n 20 "$AF_LOG_FILE" >&2 || true
    rm -f "$AF_PID_FILE"
    return 1
  fi
  echo "[server] 시작됨 (pid $pid). http://${AF_HOST/0.0.0.0/localhost}:$AF_PORT/cherry-pick"
}

cmd_stop() {
  if ! is_running; then
    echo "[server] 실행 중이 아님 (pid 파일 없음 또는 죽은 프로세스)"
    rm -f "$AF_PID_FILE"
    return 0
  fi
  local pid
  pid="$(current_pid)"
  echo "[server] 중지 (pid $pid) ..."
  kill -TERM "$pid" 2>/dev/null || true
  # 최대 5초 대기
  local waited=0
  while kill -0 "$pid" 2>/dev/null && [[ $waited -lt 50 ]]; do
    sleep 0.1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "[server] TERM 무시됨. KILL 시도."
    kill -KILL "$pid" 2>/dev/null || true
    sleep 0.2
  fi
  rm -f "$AF_PID_FILE"
  echo "[server] 중지 완료"
}

cmd_restart() {
  cmd_stop || true
  cmd_start
}

cmd_status() {
  if is_running; then
    local pid
    pid="$(current_pid)"
    echo "[server] RUNNING — pid $pid · port $AF_PORT · log $AF_LOG_FILE"
    echo "  URL : http://${AF_HOST/0.0.0.0/localhost}:$AF_PORT/cherry-pick"
    echo "  최근 로그:"
    tail -n 20 "$AF_LOG_FILE" 2>/dev/null | sed 's/^/    /'
  else
    echo "[server] STOPPED"
    if [[ -f "$AF_LOG_FILE" ]]; then
      echo "  최근 로그:"
      tail -n 20 "$AF_LOG_FILE" 2>/dev/null | sed 's/^/    /'
    fi
  fi
}

cmd_logs() {
  if [[ ! -f "$AF_LOG_FILE" ]]; then
    echo "[server] 로그 파일 없음: $AF_LOG_FILE" >&2
    return 1
  fi
  exec tail -n 100 -f "$AF_LOG_FILE"
}

cmd_dev() {
  if is_running; then
    echo "[server] 백그라운드에서 실행 중 (pid $(current_pid)). 먼저 'stop' 하세요." >&2
    return 1
  fi
  echo "[server] dev 모드 (foreground + --reload) — host=$AF_HOST port=$AF_PORT"
  exec "$UVICORN" server:app \
    --host "$AF_HOST" --port "$AF_PORT" \
    --log-level "$AF_LOG_LEVEL" --reload
}

case "${1:-start}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_restart ;;
  status)  cmd_status ;;
  logs)    cmd_logs ;;
  dev)     cmd_dev ;;
  -h|--help|help)
    sed -n '2,30p' "$0"
    ;;
  *)
    echo "사용법: $0 {start|stop|restart|status|logs|dev}" >&2
    exit 2
    ;;
esac
