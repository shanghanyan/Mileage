#!/usr/bin/env bash
# Terminal 2 on your Mac — copy live scrape JSON + HTML/PNG off the VPS.
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd "$(dirname "$0")" && pwd)/vps_env.sh"
cd "$ROOT"

mkdir -p test-results artifacts

pull_remote() {
  local remote_rel="$1"
  local local_rel="$2"
  mkdir -p "$local_rel"
  if ssh "${SSH_OPTS[@]}" "$VPS_TARGET" "test -d ${VPS_REMOTE}/${remote_rel}"; then
    rsync -avz --progress -e "$RSYNC_SSH" \
      "${VPS_TARGET}:${VPS_REMOTE}/${remote_rel}/" \
      "./${local_rel}/"
  else
    echo "Skip ${remote_rel}/ — not on the VPS yet (scrape has not written it)."
  fi
}

pull_remote "test-results" "test-results"
# Older runs used a directory name with a space.
if ssh "${SSH_OPTS[@]}" "$VPS_TARGET" "test -d ${VPS_REMOTE}/test\\ results"; then
  mkdir -p test-results
  rsync -avz --progress -e "$RSYNC_SSH" \
    "${VPS_TARGET}:${VPS_REMOTE}/test\\ results/" \
    "./test-results/"
fi
pull_remote "artifacts" "artifacts"

echo
echo "Pulled into:"
echo "  $ROOT/test-results/"
echo "  $ROOT/artifacts/"
echo "Latest scrape summaries:"
ls -ltd ./test-results/*_scrape 2>/dev/null | head -n 5 || echo "  (none yet)"
