#!/usr/bin/env bash
# Local AI Travel Intelligence Scraper — setup for macOS (Apple Silicon)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Checking Python 3.12+..."
if ! command -v python3 &>/dev/null; then
  echo "Install Python 3.12+ first: brew install python@3.12"
  exit 1
fi

PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "    Found Python $PY_VERSION"

echo "==> Creating virtual environment (optional but recommended)..."
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Installing Playwright Chromium..."
playwright install chromium

echo "==> Checking Ollama..."
if ! command -v ollama &>/dev/null; then
  echo "    Ollama not found. Install with: brew install ollama"
  echo "    Then: ollama serve  &&  ollama pull llava"
else
  if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "    Ollama is running."
    if ollama list 2>/dev/null | grep -q llava; then
      echo "    LLaVA model is available."
    else
      echo "    Pulling LLaVA model (~4 GB)..."
      ollama pull llava
    fi
  else
    echo "    Start Ollama: ollama serve"
    echo "    Then pull vision model: ollama pull llava"
  fi
fi

mkdir -p screenshots data
echo ""
echo "Setup complete. Run the scraper:"
echo "  cd \"$SCRIPT_DIR\""
echo "  source .venv/bin/activate"
echo "  python scraper.py"
