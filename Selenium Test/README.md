# Point conversion chart scrape

One-time scrape of three public partner/miles pages for **point conversion charts**. Selenium, Xvfb, and Django REST Framework all run on a single **Ubuntu VPS**.

See `SELENIUM_SCRAPING_INSTRUCTIONS.md` for contracts, tiers, and VPS sizing.

## Which cheapest VPS?

**Hetzner CX23** (2 vCPU / 4 GB / 40 GB, x86, Germany or Finland) is the pick. After the June 2026 price change it is about **€5.49/mo + IPv4 (~€0.50)** — still far cheaper than a working DigitalOcean or Linode box.

| Provider | Cheapest plan | RAM | Can run Chrome? | Working-tier price |
|----------|---------------|-----|-----------------|--------------------|
| **Hetzner** | CX23 | **4 GB** | Yes | **~€6/mo** (CX23 + IPv4) |
| DigitalOcean | Basic $4–$6 | 512 MB–1 GB | No — OOM | $24/mo (4 GB / 2 vCPU) |
| Linode (Akamai) | Nanode $5 | 1 GB | No — OOM | $24/mo (4 GB Shared) |

Do **not** buy the $4–$6 “cheapest” droplet/nanode. Chrome + Xvfb needs ~4 GB. DigitalOcean/Linode 2 GB ($12) will swap-thrash. Skip Hetzner **CAX** (ARM) — `google-chrome-stable` is amd64 only.

For a one-time job, use hourly billing and delete the server after ingest. CX23 is about €0.009/hour.

US-only egress (if Capital One blocks EU IPs): Hetzner US does not sell CX23; cheapest US 4 GB among these three is DigitalOcean Basic 4 GB in NYC ($24/mo). Try CX23 EU first.

## Layout

| Path | Role |
|------|------|
| `scrapers/` | Contracts, validation, chart detection, Chrome/Tor drivers, tier fallback |
| `tests/test_scrape_chrome.py` | Tier 2 primary (VPS) |
| `tests/test_scrape_tor.py` | Tier 3 experimental (VPS) |
| `tests/test_unit_*.py` | Fast tests (no browser) |
| `scrapes/` + `config/` | Django DRF ingest/export (localhost on the VPS) |
| `scripts/vps_bootstrap.sh` | Install Chrome, Firefox, Tor, Xvfb, tmux, swap |
| `scripts/scrape.sh` | Run scrape + ingest (auto-starts tmux) |
| `scripts/vps_ssh.sh` | Mac → VPS SSH with keepalives |
| `scripts/vps_push.sh` | Mac → VPS copy of this repo |
| `scripts/vps_pull.sh` | VPS → Mac copy of `test-results/` and `artifacts/` |
| `artifacts/` | HTML / PNG / JSON written at scrape time |
| `test-results/` | Pytest **and** live scrape JSON (one folder per run) |

---

## Test results (pytest and live scrapes)

Both unit tests and live VPS scrapes write JSON under `test-results/`. Nothing important should live only in the SSH terminal.

| Run | Folder | Files |
|-----|--------|--------|
| `pytest` | `test-results/<UTC>_pytest/` | per-test JSON, `live_chrome_*.json` / `live_tor_*.json` when those tests run, `summary.json` |
| `./scripts/scrape.sh` | `test-results/<UTC>_scrape/` | `run.json` (in progress), `<site>__tier2_chrome.json` after each attempt, `<site>.json`, `summary.json` |

Each attempt is flushed to disk immediately (atomic write + fsync) so a later crash still leaves the earlier sites on disk.

Pull that folder home with `./scripts/vps_pull.sh` after the job. Do not judge success from SSH stdout.

---

## Run a scrape from scratch (two Mac terminals)

Chrome waits can idle the SSH session. That is what produced:

`Read from remote host 62.238.117.132: Connection reset by peer` / `Broken pipe`

A dropped SSH session **kills** a scrape that was running in that shell. Use keepalives **and** tmux so the job survives.

Defaults (override with env vars if you recreate the box):

```bash
# From the project root on your Mac
export VPS_HOST=62.238.117.132
export VPS_USER=root
export VPS_REMOTE=~/selenium-test
export VPS_KEY="$PWD/key.txt"
```

### Terminal 1 — Mac: connect and stay on the VPS

```bash
cd "/Users/avacheng/Code Projects/Pointy/Selenium Test"
chmod 600 key.txt
./scripts/vps_ssh.sh
```

Equivalent raw SSH:

```bash
ssh -i "$PWD/key.txt" \
  -o IdentitiesOnly=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=6 \
  -o TCPKeepAlive=yes \
  root@62.238.117.132
```

You should land on `root@ubuntu-4gb-hel1-1`. Leave this terminal open. If it ever dies, reconnect with the same command, then:

```bash
tmux attach -t scrape
```

### Terminal 2 — Mac: copy this repo up

Do this **before** every scrape so the VPS has the latest detector/code, `.env`, and `vm_profile.json`.

```bash
cd "/Users/avacheng/Code Projects/Pointy/Selenium Test"
./scripts/vps_push.sh
```

Confirm `.env` and `vm_profile.json` exist on the box (Terminal 1):

```bash
cd ~/selenium-test
test -f .env && test -f vm_profile.json && echo ok
```

If that does not print `ok`, run `./scripts/vps_push.sh` again from Terminal 2. `DJANGO_SECRET_KEY` in `.env` must be quoted (already is in the local file).

### First time only (Terminal 1, on the VPS)

Skip this if Chrome/`xvfb-run`/Python venv already exist.

```bash
cd ~/selenium-test
sudo bash scripts/vps_bootstrap.sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`bootstrap` installs Chrome, Firefox, Tor, Xvfb, **tmux**, and 2 GB swap. `REMOTE_SCRAPE=1` is required and is already set in `.env`.

If this box was bootstrapped before tmux was added:

```bash
sudo apt install -y tmux
```

### Run the scrape (Terminal 1, on the VPS)

```bash
cd ~/selenium-test
source .venv/bin/activate
./scripts/scrape.sh --no-tor
```

`scrape.sh` re-launches itself inside **tmux session `scrape`**. You should see a `========== Pointy scrape ==========` banner and then lines like `[scrape] capitalone_venture_partners: starting`.

The green/grey bar at the bottom is tmux, not the scrape finishing:

```
[scrape] 0:scrape*                          "ubuntu-4gb-hel1-1" 21:35 20-Sep-26
```

| What you see | Meaning |
|--------------|---------|
| Banner + `[scrape] … starting` | Working. Chrome can sit silent for several minutes per site. |
| `[exited]` immediately | Old tmux wrapper. Push latest code, then kill/re-run (below). |
| Only a bar and `0:bash*` with an empty pane | Scrape did **not** start. Kill and re-run (see below). |
| `Deliverable met for N/3` or `[scrape finished, exit N]` | Done. |

If tmux prints `[exited]` or you have a blank session:

```bash
tmux kill-session -t scrape 2>/dev/null || true
cd ~/selenium-test
source .venv/bin/activate
./scripts/scrape.sh --no-tor
```

Push the latest repo from Mac Terminal 2 (`./scripts/vps_push.sh`) **before** that re-run.

Do **not** run `./scripts/vps_pull.sh` until a scrape has actually written `test-results/` on the VPS.

| While it runs | What to do |
|---------------|------------|
| Watch progress | Stay in Terminal 1. Lines appear after each HTTP/Chrome attempt. |
| Detach without stopping | `Ctrl-b` then `d` |
| SSH dies (`Broken pipe`) | Terminal 1: `./scripts/vps_ssh.sh` then `tmux attach -t scrape` |
| Already running | `tmux attach -t scrape` — do not start a second scrape |

`--no-tor` is the usual full Chrome run. Other flags:

```bash
./scripts/scrape.sh                 # Chrome, then experimental Tor
./scripts/scrape.sh --tier1-only    # HTTP only, no browser
./scripts/scrape.sh --site capitalone_venture_partners
```

When it finishes, Terminal 1 prints a path like `test-results/20260921T043000Z_scrape/` and `Deliverable met for N/3 site(s).` The JSON in that folder is the record of the live scrape.

### Terminal 2 — Mac: copy results home

```bash
cd "/Users/avacheng/Code Projects/Pointy/Selenium Test"
./scripts/vps_pull.sh
```

Then open:

- `test-results/<timestamp>_scrape/summary.json` — per-site `deliverable_met`
- `test-results/<timestamp>_scrape/<site>.json` — full fallback
- `test-results/<timestamp>_scrape/<site>__tier2_chrome.json` — Chrome attempt (written as soon as Chrome finishes)
- `artifacts/chrome/*.html` / `*.png` — captured pages

### Optional: Django export (second SSH session)

Export API stays on loopback. From a **second** `./scripts/vps_ssh.sh` (or after detaching tmux):

```bash
cd ~/selenium-test
source .venv/bin/activate
python manage.py migrate
gunicorn --bind 127.0.0.1:8000 config.wsgi:application
```

From that same box:

```bash
curl http://127.0.0.1:8000/api/exports/deliverables/
```

`./scripts/scrape.sh` already migrates and ingests after the scrape.

### After you are done

Copy `test-results/` and `artifacts/` home first. Then delete the Hetzner box if it was hourly. Closing Terminal 1 does **not** stop billing; deleting the VPS does.

---

## Fast unit tests (laptop, no browser)

```bash
source .venv/bin/activate
pytest tests/test_unit_core.py tests/test_unit_tier_fallback.py tests/test_unit_django_ingest.py -m unit -v
```

Those results also land in `test-results/<timestamp>_pytest/`. They do not scrape live sites.

Live Chrome/Tor pytest (VPS only, after `REMOTE_SCRAPE=1`):

```bash
cd ~/selenium-test
source .venv/bin/activate
xvfb-run -a pytest tests/test_scrape_chrome.py -m chrome -v
```

Each Chrome/Tor test writes `live_chrome_<site>.json` / `live_tor_<site>.json` into that pytest results folder **before** the assertion, so a failed chart still has a recorded scrape.
