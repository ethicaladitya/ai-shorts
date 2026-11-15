#!/bin/bash
# =============================================================================
# Persona System & AI Shorts — Easy Setup & Deploy
# =============================================================================

set -e

# --- 1. System Check ---
echo "Checking system requirements..."
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker not found. Please install Docker."
    exit 1
fi

# --- 2. Local Setup (for development) ---
echo "Setting up local environment..."
if [ ! -d "backend/.venv" ]; then
    python3.12 -m venv backend/.venv
fi
source backend/.venv/bin/activate
pip install -r backend/requirements.txt

# --- 3. Model Preparation ---
echo "Preparing models..."
if command -v ollama &> /dev/null; then
    echo "Pulling Qwen for local generation..."
    ollama pull qwen2.5:7b || echo "Ollama pull failed, skipping."
else
    echo "Ollama not found. Cloud fallback (Azure) will be used."
fi

# --- 4. Deployment ---
echo "Starting deployment to production..."
if [ -f "deploy.sh" ]; then
    ./deploy.sh nodejs
else
    echo "ERROR: deploy.sh not found."
    exit 1
fi

echo "Done! Dashboard available at: https://video.adityashah.blog/persona/"
