#!/usr/bin/env bash
# Terminal 2 on your Mac — copy this repo (including .env / vm_profile.json) to the VPS.
# Does not copy .venv, artifacts, or test results.
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd "$(dirname "$0")" && pwd)/vps_env.sh"
cd "$ROOT"

rsync -avz --progress \
  -e "$RSYNC_SSH" \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude 'artifacts/' \
  --exclude 'test results/' \
  --exclude 'Test Results/' \
  --exclude 'test-results/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude '.git/' \
  --exclude 'db.sqlite3' \
  --exclude '*.sqlite3' \
  ./ "${VPS_TARGET}:${VPS_REMOTE}/"

echo
echo "Pushed to ${VPS_TARGET}:${VPS_REMOTE}/"
echo "On the VPS: cd ${VPS_REMOTE} && ./scripts/scrape.sh --no-tor"
