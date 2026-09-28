#!/usr/bin/env bash
# Ubuntu 22.04/24.04 x86_64 — Chrome + Xvfb + Firefox + Tor + Python.
# Do not use ARM images (Hetzner CAX): google-chrome-stable is amd64 only.
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/vps_bootstrap.sh" >&2
  exit 1
fi

if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
  echo "This scrape stack needs an x86_64 VPS. Recreate the server as CX/CPX, not CAX." >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  ca-certificates curl gnupg unzip \
  python3 python3-venv python3-pip \
  xvfb xauth \
  tmux \
  tor \
  fonts-liberation fonts-noto-core fonts-noto-cjk

# Google Chrome (amd64)
install -d -m 0755 /usr/share/keyrings
rm -f /usr/share/keyrings/google-chrome.gpg
curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
  | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
  > /etc/apt/sources.list.d/google-chrome.list

# Mozilla Firefox .deb (avoid Ubuntu 24.04 snap, which breaks Selenium)
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://packages.mozilla.org/apt/repo-signing-key.gpg \
  -o /etc/apt/keyrings/packages.mozilla.org.asc
echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" \
  > /etc/apt/sources.list.d/mozilla.list
cat > /etc/apt/preferences.d/mozilla <<'EOF'
Package: *
Pin: origin packages.mozilla.org
Pin-Priority: 1000
EOF

apt-get update
apt-get install -y google-chrome-stable firefox

systemctl enable --now tor

# 2 GB swap so Chrome on a 4 GB box does not OOM on JS-heavy pages
if [[ ! -f /swapfile ]]; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

timedatectl set-timezone America/Los_Angeles || true
timedatectl set-ntp true || true

if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH
  ufw --force enable
fi

echo
echo "Bootstrap done. Next:"
echo "  cd /path/to/project"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  cp .env.example .env && cp vm_profile.example.json vm_profile.json"
echo "  ./scripts/scrape.sh --no-tor"
echo "  (runs inside tmux session 'scrape'; reattach with: tmux attach -t scrape)"
