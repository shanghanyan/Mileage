#!/usr/bin/env bash
# Terminal 1 on your Mac — SSH into the VPS with keepalives.
# Usage: ./scripts/vps_ssh.sh
#        ./scripts/vps_ssh.sh 'tmux attach -t scrape'
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd "$(dirname "$0")" && pwd)/vps_env.sh"

if [[ ! -f "$VPS_KEY" ]]; then
  echo "Missing $VPS_KEY" >&2
  exit 2
fi
chmod 600 "$VPS_KEY" 2>/dev/null || true

exec ssh "${SSH_OPTS[@]}" "$VPS_TARGET" "$@"
