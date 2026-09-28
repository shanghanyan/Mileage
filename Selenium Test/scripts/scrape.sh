#!/usr/bin/env bash
# Run the scrape on this VPS, write JSON under test-results/, then ingest artifacts.
# Starts a tmux session so an SSH drop (Broken pipe) does not kill Chrome.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
chmod +x "$ROOT/scripts/scrape.sh" 2>/dev/null || true

if [[ -z "${TMUX:-}" && "${SCRAPE_ALLOW_BARE_SSH:-}" != "1" ]] && command -v tmux >/dev/null 2>&1; then
  if tmux has-session -t scrape 2>/dev/null; then
    echo "tmux session 'scrape' is already running — attaching." >&2
    exec tmux attach -t scrape
  fi
  quoted=()
  for arg in "$@"; do
    quoted+=("$(printf '%q' "$arg")")
  done
  inner="export SCRAPE_ALLOW_BARE_SSH=1; "
  inner+="cd $(printf '%q' "$ROOT"); "
  inner+="bash $(printf '%q' "$ROOT/scripts/scrape.sh") ${quoted[*]}; "
  inner+="status=\$?; echo; echo \"[scrape finished, exit \$status]\"; exec bash"
  echo "Starting tmux session 'scrape'." >&2
  echo "Detach: Ctrl-b then d.  Reattach: tmux attach -t scrape" >&2
  tmux new-session -d -s scrape -n scrape -c "$ROOT" "$inner"
  exec tmux attach -t scrape
fi

if [[ -f .env ]]; then
  set +e
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  set -e
fi

export REMOTE_SCRAPE="${REMOTE_SCRAPE:-1}"
export SCRAPE_ARTIFACTS_DIR="${SCRAPE_ARTIFACTS_DIR:-artifacts}"
export TEST_RESULTS_DIR="${TEST_RESULTS_DIR:-test-results}"
export TOR_SOCKS="${TOR_SOCKS:-127.0.0.1:9050}"
export PYTHONUNBUFFERED=1

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo >&2
echo "========== Pointy scrape ==========" >&2
echo "cwd:     $ROOT" >&2
echo "args:    ${*:-<none>}" >&2
echo "results: ${TEST_RESULTS_DIR}/" >&2
echo "python:  $(command -v python || true)" >&2
echo "Chrome can sit here for several minutes per site. This is not stuck." >&2
echo "Detach: Ctrl-b then d.  If SSH drops: tmux attach -t scrape" >&2
echo "===================================" >&2
echo >&2

if ! command -v python >/dev/null 2>&1; then
  echo "python not found. Activate the venv: source .venv/bin/activate" >&2
  exit 3
fi

status=0
python -u -m scrapers.run "$@" || status=$?

python manage.py migrate --noinput || true
python manage.py ingest_artifacts || true

echo >&2
echo "Live scrape JSON is under: ${TEST_RESULTS_DIR}/" >&2
if [[ -d "${TEST_RESULTS_DIR}" ]]; then
  ls -ltd "${TEST_RESULTS_DIR}"/*_scrape 2>/dev/null | head -n 5 >&2 || true
fi
echo "HTML / PNG / per-attempt JSON: ${SCRAPE_ARTIFACTS_DIR}/" >&2
exit "$status"
