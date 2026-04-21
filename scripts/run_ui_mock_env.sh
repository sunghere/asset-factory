#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOCK_ROOT="${ASSET_FACTORY_MOCK_ROOT:-$ROOT_DIR/data/mock-ui}"
MOCK_DB="${ASSET_FACTORY_DB_PATH:-$MOCK_ROOT/asset-factory.db}"
MOCK_DATA="${ASSET_FACTORY_DATA_DIR:-$MOCK_ROOT}"
MOCK_EXPORT="${ASSET_FACTORY_EXPORT_ROOT:-$MOCK_ROOT/export}"
HOST="${AF_HOST:-127.0.0.1}"
PORT="${AF_PORT:-18000}"

mkdir -p "$MOCK_DATA" "$MOCK_EXPORT" "$MOCK_DATA/candidates" "$MOCK_DATA/approved"

export ASSET_FACTORY_MOCK_MODE=1
export ASSET_FACTORY_DB_PATH="$MOCK_DB"
export ASSET_FACTORY_DATA_DIR="$MOCK_DATA"
export ASSET_FACTORY_EXPORT_ROOT="$MOCK_EXPORT"
export ASSET_FACTORY_ALLOWED_ROOTS="${ASSET_FACTORY_ALLOWED_ROOTS:-$MOCK_DATA:$MOCK_EXPORT}"

echo "[mock-env] root=$MOCK_ROOT"
echo "[mock-env] db=$ASSET_FACTORY_DB_PATH"
echo "[mock-env] data=$ASSET_FACTORY_DATA_DIR"
echo "[mock-env] export=$ASSET_FACTORY_EXPORT_ROOT"

"$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/seed_ui_mock_all_screens.py"

echo "[mock-env] starting server at http://$HOST:$PORT"
echo "[mock-env] smoke routes:"
echo "  - http://$HOST:$PORT/app/"
echo "  - http://$HOST:$PORT/app/queue"
echo "  - http://$HOST:$PORT/app/cherry-pick"
echo "  - http://$HOST:$PORT/app/assets"
echo "  - http://$HOST:$PORT/app/batches"
echo "  - http://$HOST:$PORT/app/catalog"
echo "  - http://$HOST:$PORT/app/export"
echo "  - http://$HOST:$PORT/app/system"
echo "  - http://$HOST:$PORT/app/settings"

exec "$ROOT_DIR/.venv/bin/uvicorn" server:app --host "$HOST" --port "$PORT" --reload
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export ASSET_FACTORY_DATA_DIR="${ASSET_FACTORY_DATA_DIR:-$ROOT_DIR/data/mock-ui}"
export ASSET_FACTORY_DB_PATH="${ASSET_FACTORY_DB_PATH:-$ASSET_FACTORY_DATA_DIR/asset-factory.db}"
export ASSET_FACTORY_EXPORT_ROOT="${ASSET_FACTORY_EXPORT_ROOT:-$ASSET_FACTORY_DATA_DIR/export}"
export ASSET_FACTORY_MOCK_MODE=1

mkdir -p "$ASSET_FACTORY_DATA_DIR/candidates" "$ASSET_FACTORY_DATA_DIR/approved" "$ASSET_FACTORY_EXPORT_ROOT"

PORT="${AF_PORT:-8000}"
HOST="${AF_HOST:-0.0.0.0}"

.venv/bin/uvicorn server:app --host "$HOST" --port "$PORT" --reload &
SERVER_PID=$!
cleanup() { kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT

for _ in {1..30}; do
  if curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null; then
    break
  fi
  sleep 0.5
done

.venv/bin/python scripts/seed_ui_mock_all_screens.py

echo
echo "[mock-ui] running with isolated roots"
echo "  DATA_DIR=$ASSET_FACTORY_DATA_DIR"
echo "  DB_PATH=$ASSET_FACTORY_DB_PATH"
echo "  EXPORT_ROOT=$ASSET_FACTORY_EXPORT_ROOT"
echo
echo "Smoke URLs:"
echo "  http://127.0.0.1:${PORT}/app/"
echo "  http://127.0.0.1:${PORT}/app/dashboard"
echo "  http://127.0.0.1:${PORT}/app/queue"
echo "  http://127.0.0.1:${PORT}/app/cherry-pick/${BATCH_ID:-btc_uimock_char}"
echo "  http://127.0.0.1:${PORT}/app/assets"
echo "  http://127.0.0.1:${PORT}/app/batches"
echo "  http://127.0.0.1:${PORT}/app/export"
echo "  http://127.0.0.1:${PORT}/app/system"
echo "  http://127.0.0.1:${PORT}/app/settings"
echo
wait "$SERVER_PID"
