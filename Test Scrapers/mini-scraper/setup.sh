#!/usr/bin/env bash
set -euo pipefail

# One-time setup for the Capital One Point Optimizer.
# Idempotent: safe to re-run. `run.sh` performs the same checks, so this is
# mainly a convenience for a clean first install.

echo "→ Setting up Capital One Point Optimizer..."

# ── Pick a compatible Python (3.11 or 3.12; 3.13 lacks wheels for pinned deps) ──
PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null && "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) and sys.version_info < (3,13) else 1)" 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "ERROR: need Python 3.11 or 3.12 (3.13 has wheel gaps for pinned deps)." && exit 1
fi
echo "✓ Using $($PYTHON --version)"

# ── Virtual environment ──────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
    "$PYTHON" -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

# ── Dependencies + browser ───────────────────────────────────────────────────
pip install -q -U pip
pip install -r requirements.txt
python -m playwright install chromium

# ── Environment file ─────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "→ Created .env from .env.example"
fi

mkdir -p logs storage config

echo "✓ Setup complete — run ./run.sh to start the optimizer"
