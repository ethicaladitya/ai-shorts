#!/usr/bin/env bash
# restart.sh — Clean restart for AI Shorts backend
# Kills by port to ensure no leftover workers hold the socket.

set -e

PORT=8787
BACKEND_DIR="$(cd "$(dirname "$0")/backend" && pwd)"
LOG="$BACKEND_DIR/data/app.log"

echo "🛑 Killing any process on port $PORT..."
lsof -ti:$PORT | xargs kill -9 2>/dev/null || true
sleep 2

echo "🚀 Starting backend (single process, port $PORT)..."
PYTHONMALLOC=malloc \
PYTHONPATH="$BACKEND_DIR" \
"$BACKEND_DIR/.venv/bin/uvicorn" app.main:app \
  --host 127.0.0.1 \
  --port $PORT \
  --log-level info >> "$LOG" 2>&1 &

sleep 5

STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$PORT/persona/health 2>/dev/null || echo "000")
if [ "$STATUS" = "200" ]; then
  echo "✅ Backend live at http://127.0.0.1:$PORT/persona/"
else
  echo "❌ Backend did not start (HTTP $STATUS). Check logs: tail -f $LOG"
  exit 1
fi
