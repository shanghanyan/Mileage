#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   AggregateScraper — C1 Miles Optimizer  ║"
echo "╚══════════════════════════════════════════╝"
echo ""

PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null && "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.11+ required." && exit 1
fi

if [ ! -d "venv" ]; then
    echo "→ Creating virtual environment..."
    "$PYTHON" -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

pip install -q -r requirements.txt
mkdir -p logs storage config

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "→ Created .env from .env.example"
fi

"$PYTHON" main.py "$@"
