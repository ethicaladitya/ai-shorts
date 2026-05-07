#!/usr/bin/env bash
# AI Shorts — local backend start script
# Runs uvicorn from the venv, with all paths resolved to the local data dir.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
DATA_DIR="$SCRIPT_DIR/data"
LOG_FILE="$DATA_DIR/app.log"

# Ensure PATH includes brew binaries (FFmpeg etc.)
# Prefer ffmpeg-full (includes libass for subtitle rendering) over standard ffmpeg bottle
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# Override container-centric paths with local ones
export DATABASE_URL="sqlite:///${DATA_DIR}/ai_shorts.db"
export DATA_DIR="$DATA_DIR"
export OUTPUT_DIR="$DATA_DIR/output"
export TEMP_DIR="$DATA_DIR/temp"
export ASSETS_DIR="$DATA_DIR"
export PYTHONMALLOC=malloc

# Load .env from project root (one level up from backend/)
if [[ -f "$SCRIPT_DIR/../.env" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip blank lines and comments
        [[ -z "$line" || "$line" =~ ^\s*# ]] && continue
        # Export each KEY=value line
        export "$line" 2>/dev/null || true
    done < "$SCRIPT_DIR/../.env"
fi

# Source the project .env (re-export anything already loaded is fine, set -a handles duplicates)
mkdir -p "$DATA_DIR/output" "$DATA_DIR/temp" "$DATA_DIR/assets" "$DATA_DIR/faces" "$DATA_DIR/kokoro"

exec "$VENV/bin/uvicorn" app.main:app \
    --host 127.0.0.1 \
    --port 8787 \
    --workers 1 \
    --log-level info
