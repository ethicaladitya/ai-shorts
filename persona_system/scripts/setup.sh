#!/bin/bash
# persona_system/scripts/setup.sh
# One-time setup for the persona system on Mac Mini M1/M2

set -e
echo "=== Persona System Setup ==="

# 1. Create venv
python3.11 -m venv persona_system/.venv
source persona_system/.venv/bin/activate

# 2. Install deps
pip install --upgrade pip
pip install -r persona_system/requirements.txt

# 3. Playwright browsers
playwright install chromium

# 4. Init DB
python -c "from persona_system.shared.database import init_db; init_db(); print('DB ready')"

# 5. Ollama model pull (local LLM)
if command -v ollama &>/dev/null; then
  ollama pull qwen2.5:7b && echo "Qwen model ready"
else
  echo "WARN: Ollama not found. Install from https://ollama.com"
fi

# 6. Media dirs
mkdir -p persona_system/media/{images,videos,audio}
mkdir -p persona_system/data

echo ""
echo "=== Setup complete! ==="
echo "Start dashboard:  python -m persona_system.dashboard.api"
echo "Run pipeline:     python -m persona_system.scripts.run_pipeline --persona default"
echo "Publish posts:    python -m persona_system.automation.publisher"
