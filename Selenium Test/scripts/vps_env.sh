#!/usr/bin/env bash
# Shared VPS connection settings. Override with env vars if the box is recreated.
VPS_HOST="${VPS_HOST:-62.238.117.132}"
VPS_USER="${VPS_USER:-root}"
VPS_REMOTE="${VPS_REMOTE:-~/selenium-test}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPS_KEY="${VPS_KEY:-$ROOT/key.txt}"

if [[ ! -f "$VPS_KEY" ]]; then
  echo "SSH key not found: $VPS_KEY" >&2
  exit 2
fi

SSH_OPTS=(
  -i "$VPS_KEY"
  -o IdentitiesOnly=yes
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=6
  -o TCPKeepAlive=yes
)

# Quoted for rsync -e (project path contains spaces).
RSYNC_SSH="ssh -i '$VPS_KEY' -o IdentitiesOnly=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=6 -o TCPKeepAlive=yes"

VPS_TARGET="${VPS_USER}@${VPS_HOST}"
