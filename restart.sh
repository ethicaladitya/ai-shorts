#!/usr/bin/env bash
# =============================================================================
# AI Shorts — local restart script
# Run this after editing .env to kill the running backend and start fresh.
# Usage: ./restart.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="/tmp/ai-shorts-backend.pid"
TUNNEL_PID_FILE="/tmp/ai-shorts-tunnel.pid"
LOG_FILE="$SCRIPT_DIR/backend/data/app.log"
TUNNEL_LOG="/tmp/cloudflared-ai-shorts.log"
HEALTH_URL="http://127.0.0.1:8787/health"
TUNNEL_CONFIG="$HOME/.cloudflared/ai-shorts.yml"

# ─── Colors ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[ok]${NC}    $*"; }
info() { echo -e "${YELLOW}[...]${NC}   $*"; }
fail() { echo -e "${RED}[fail]${NC}  $*"; exit 1; }

# ─── Step 1: Kill existing backend ───────────────────────────────────────────
info "Stopping existing backend..."

# Try PID file first
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID"
        ok "Stopped PID $OLD_PID"
    else
        info "PID $OLD_PID from pid file is no longer running"
    fi
    rm -f "$PID_FILE"
fi

# Kill any remaining uvicorn workers for this app (e.g. --workers 2 spawns children)
LEFTOVER=$(pgrep -f "uvicorn app.main" 2>/dev/null || true)
if [[ -n "$LEFTOVER" ]]; then
    echo "$LEFTOVER" | xargs kill 2>/dev/null || true
    ok "Killed leftover worker(s): $LEFTOVER"
fi

sleep 1

# ─── Step 2: Kill existing tunnel ────────────────────────────────────────────
info "Stopping existing Cloudflare tunnel..."
if [[ -f "$TUNNEL_PID_FILE" ]]; then
    OLD_TPID=$(cat "$TUNNEL_PID_FILE")
    if kill -0 "$OLD_TPID" 2>/dev/null; then
        kill "$OLD_TPID"
        ok "Stopped tunnel PID $OLD_TPID"
    else
        info "Tunnel PID $OLD_TPID no longer running"
    fi
    rm -f "$TUNNEL_PID_FILE"
fi
# Kill any stray cloudflared processes for this tunnel
pkill -f "cloudflared tunnel --config.*ai-shorts" 2>/dev/null || true

# ─── Step 2: Restart backend ─────────────────────────────────────────────────
info "Starting backend (log → $LOG_FILE)..."
mkdir -p "$SCRIPT_DIR/backend/data"

# nohup + disown fully detaches uvicorn and its worker children from this
# terminal, preventing SIGTTOU from killing workers when backgrounded.
# The subshell cd ensures uvicorn can find the 'app' module regardless of
# where restart.sh is called from.
nohup bash -c "cd '$SCRIPT_DIR/backend' && exec bash start.sh" >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
disown "$NEW_PID"
echo "$NEW_PID" > "$PID_FILE"
ok "Backend started with PID $NEW_PID"

# ─── Step 3: Start Cloudflare tunnel ─────────────────────────────────────────
if [[ -f "$TUNNEL_CONFIG" ]]; then
    info "Starting Cloudflare tunnel..."
    nohup cloudflared tunnel --config "$TUNNEL_CONFIG" run >> "$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!
    echo "$TUNNEL_PID" > "$TUNNEL_PID_FILE"
    ok "Tunnel started with PID $TUNNEL_PID (log → $TUNNEL_LOG)"
else
    info "No tunnel config found at $TUNNEL_CONFIG — skipping"
fi

# ─── Step 4: Wait for health ─────────────────────────────────────────────────
info "Waiting for /health..."
for i in $(seq 1 30); do
    if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
        HEALTH=$(curl -sf "$HEALTH_URL")
        ok "Backend is healthy: $HEALTH"
        if [[ -f "$TUNNEL_PID_FILE" ]]; then
            TPID=$(cat "$TUNNEL_PID_FILE")
            if kill -0 "$TPID" 2>/dev/null; then
                ok "Tunnel is running (PID $TPID) → https://video.adityashah.blog"
            fi
        fi
        echo ""
        info "Last log lines:"
        tail -6 "$LOG_FILE"
        exit 0
    fi
    sleep 0.5
done

fail "Backend did not become healthy after 10s. Check: tail -f $LOG_FILE"
