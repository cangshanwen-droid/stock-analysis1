#!/bin/bash
set -euo pipefail

PORT="${PORT:-8000}"

# ── Database readiness ────────────────────────────────────────────────
# Wait for PostgreSQL if DATABASE_URL is set, otherwise SQLite is local
if [ -n "${DATABASE_URL:-}" ]; then
    echo "Waiting for PostgreSQL to be ready..."
    MAX_RETRIES=30
    RETRY_INTERVAL=2
    RETRY=0
    while [ $RETRY -lt $MAX_RETRIES ]; do
        if python -c "
import os, sys
from psycopg import connect
try:
    conn = connect(os.environ['DATABASE_URL'], connect_timeout=3)
    conn.execute('SELECT 1')
    conn.close()
except Exception:
    sys.exit(1)
" 2>/dev/null; then
            echo "PostgreSQL is ready."
            break
        fi
        RETRY=$((RETRY + 1))
        echo "  Attempt ${RETRY}/${MAX_RETRIES} — retrying in ${RETRY_INTERVAL}s..."
        sleep $RETRY_INTERVAL
    done
    if [ $RETRY -ge $MAX_RETRIES ]; then
        echo "ERROR: PostgreSQL did not become ready after ${MAX_RETRIES} attempts."
        exit 1
    fi
fi

# ── Graceful shutdown handler ─────────────────────────────────────────
cleanup() {
    echo "Shutting down gracefully..."
    # Send TERM to uvicorn, wait up to 15s, then force-quit
    if [ -n "${UVICORN_PID:-}" ]; then
        kill -TERM "$UVICORN_PID" 2>/dev/null || true
        wait "$UVICORN_PID" 2>/dev/null || true
    fi
    echo "Shutdown complete."
    exit 0
}

trap cleanup SIGTERM SIGINT

# ── Start uvicorn ─────────────────────────────────────────────────────
echo "Starting uvicorn on 0.0.0.0:${PORT} (workers=${UVICORN_WORKERS:-4})..."
uvicorn api.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --workers "${UVICORN_WORKERS:-4}" \
    --timeout-keep-alive 5 \
    --graceful-timeout 10 &
UVICORN_PID=$!
wait "$UVICORN_PID"
