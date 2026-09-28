#!/usr/bin/env bash
set -euo pipefail

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Capital One Point Optimizer            ║"
echo "╚══════════════════════════════════════════╝"
echo ""

PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null && "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) and sys.version_info < (3,13) else 1)" 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    if command -v python3 &>/dev/null; then
        PYTHON=python3
    else
        echo "ERROR: python3 not found. Install Python 3.11 or 3.12 (3.13 has wheel gaps for pinned deps)." && exit 1
    fi
fi
PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"; then
    echo "✓ Python $PY_VERSION"
else
    echo "ERROR: Python 3.11+ required (found $PY_VERSION)" && exit 1
fi

if [ ! -d "venv" ]; then
    echo "→ Creating virtual environment..."
    "$PYTHON" -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
echo "✓ Virtual environment active"

echo "→ Installing/verifying dependencies..."
pip install -q -r requirements.txt
echo "✓ Dependencies ready"

# Self-heal: ensure the Chromium browser binary Playwright needs is present.
# Runs every time (cheap no-op once installed) so a missing browser can't break a run.
echo "→ Ensuring Playwright Chromium is installed..."
if "$PYTHON" -m playwright install chromium 2>/dev/null; then
    echo "✓ Playwright Chromium ready"
else
    echo "  ⚠ Chromium install failed — run manually: python3 -m playwright install chromium"
fi

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "→ Created .env from .env.example (edit OLLAMA_MODEL if needed)"
fi

mkdir -p logs storage config

echo ""
echo "→ Checking Ollama (required only for vision scrapers)..."

# Auto-start Ollama in the background if it's installed but not yet running.
STARTED_OLLAMA=false
if ! curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1; then
    if command -v ollama &>/dev/null; then
        echo "  → Ollama not running — starting in background..."
        ollama serve &>/dev/null &
        sleep 2
        if curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1; then
            STARTED_OLLAMA=true
        fi
    fi
fi

if curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1; then
    echo "✓ Ollama is running"
    MODEL=$(grep -E '^OLLAMA_MODEL=' .env 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "llama3.2-vision:11b")
    MODEL=${MODEL:-llama3.2-vision:11b}
    echo "  → Pulling model '$MODEL' if not already present..."
    ollama pull "$MODEL" 2>/dev/null && echo "  ✓ Model ready" || echo "  ⚠ Could not pull '$MODEL'; vision scrapers will use cache"
else
    echo "  ⚠ Ollama not running — vision scrapers (LifeMiles, Turkish) will use cached rates"
    echo "  To enable: open a new terminal and run:  ollama serve"
    echo "  Then pull a model:  ollama pull llama3.2-vision:11b   (~8GB)"
    echo "  Lighter option:     ollama pull moondream2             (~2GB, less accurate)"
fi

echo ""
echo "━━━ Starting optimizer run ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
"$PYTHON" main.py
echo ""
echo "━━━ Done. Logs are in logs/  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
