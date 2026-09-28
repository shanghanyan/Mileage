# Selenium Scraping Instructions

One-time scrape of airline and credit-card partner pages to extract **point conversion charts** (award charts / points tables). Selenium, browsers, and Django REST Framework run on a single **Ubuntu VPS**. Chrome and Firefox stay **non-headless** and use **Xvfb** for a virtual display.

**Primary deliverable:** point conversion chart found and exported—not raw HTML alone.

---

## Table of Contents

1. [Overview](#overview)
2. [Which VPS](#which-vps)
3. [Hard Limits (VPS)](#hard-limits-vps)
4. [Primary Deliverable](#primary-deliverable)
5. [Tiered Fallback](#tiered-fallback)
6. [Two Selenium Tests](#two-selenium-tests)
7. [Target Pages and Per-Site Scrape Contracts](#target-pages-and-per-site-scrape-contracts)
8. [Success / Failure Validation](#success--failure-validation)
9. [Bot Manager Detection](#bot-manager-detection)
10. [Capture at Scrape Time](#capture-at-scrape-time)
11. [Architecture](#architecture)
12. [VPS Setup](#vps-setup)
13. [Network Egress](#network-egress)
14. [Device Fingerprinting](#device-fingerprinting)
15. [Timezone](#timezone)
16. [Login Mimicry (Fallback Only)](#login-mimicry-fallback-only)
17. [Session Isolation](#session-isolation)
18. [Speed, Memory, Redirects](#speed-memory-redirects)
19. [Operational Safeguards](#operational-safeguards)
20. [Django REST Framework](#django-rest-framework)
21. [Running the Tests](#running-the-tests)
22. [One-Time Scrape Checklist](#one-time-scrape-checklist)
23. [Security and Legal Notes](#security-and-legal-notes)

---

## Overview

This project performs a **one-time scrape** of three public partner/miles pages.

| Layer | Where | Role |
|-------|-------|------|
| Selenium + Chrome / Firefox | **Ubuntu VPS** | Scrape, validate, write artifacts |
| Django + DRF | **Same VPS** (localhost) | Ingest artifacts, export chart JSON |
| Laptop browsers | **Never used** | Personal Chrome/Safari profiles stay off the path |

Pipeline:

1. **Tier 1** — HTTP fetch (`httpx`, no browser)
2. **Tier 2** — Google Chrome + Xvfb, VPS public IP (primary)
3. **Tier 3** — Tor + Firefox + Xvfb (experimental)
4. **Tier 4** — Login mimicry (only if validation returns `LOGIN_REQUIRED`)

Return data at the first tier where the point conversion chart validates.

---

## Which VPS

Chrome + Xvfb needs **2 vCPU and 4 GB RAM**. The $4–$6 “cheapest” plans at DigitalOcean and Linode are 512 MB–1 GB and will OOM.

| | Hetzner CX23 | DigitalOcean Basic 4 GB | Linode Shared 4 GB |
|---|---|---|---|
| vCPU / RAM / disk | 2 / 4 GB / 40 GB | 2 / 4 GB / 80 GB | 2 / 4 GB / 80 GB |
| Price (2026) | **~€5.49 + €0.50 IPv4** | $24/mo | $24/mo |
| Transfer | 20 TB (EU) | 4 TB | 4 TB |
| Regions for this size | DE / FI only | Global | Global |
| Chrome-ready cheapest SKU? | **Yes** | No ($4–$12 too small) | No ($5–$12 too small) |

**Buy Hetzner CX23 (x86, not CAX ARM) with a public IPv4.** Use hourly billing and delete the box after the job.

If a US site blocks the EU datacenter IP, the cheapest *US* 4 GB among these three is DigitalOcean Basic 4 GB in NYC ($24). Hetzner US does not sell CX23.

---

## Hard Limits (VPS)

| Limit | What it means | Mitigation |
|-------|---------------|------------|
| **Datacenter IP** | Capital One / Cathay / JAL see a Hetzner or DO range, not a household ISP. Bot managers treat this more harshly than a home IP. | Fresh profile per site; rate-limit; log bot-block outcomes; try Tor only as a probe. |
| **Shared CPU** | CX23 / Basic droplets share cores. Chrome can hitch under noisy neighbors. | Sequential scrapes only; 2 GB swap (bootstrap script). |
| **No physical display** | “Non-headless” still needs a display server. | `xvfb-run` (auto-wrapped by `python -m scrapers.run`). |
| **Geo** | EU IP may change copy or trigger region walls on US/JP pages. | Contracts already check `wrong_region`. Recreate in a US region only if that fires. |

---

## Primary Deliverable

| Deliverable | Definition |
|-------------|------------|
| **Point conversion chart** | Table or grid listing mile/point conversion ratios, transfer partners, or award tiers |
| **Success** | Chart detected with confidence ≥ 0.45, bot-block checks pass, page content thresholds met |
| **Failure** | Any outcome in [Success / Failure Validation](#success--failure-validation) without chart |

Aliases map to canonical URLs via `scrapers/contracts.py` and `redirects.yaml`.

---

## Tiered Fallback

| Tier | Method | Browser / transport | Data returned when |
|------|--------|---------------------|-------------------|
| **1** | `tier1_http_fetch()` | `httpx` GET | Static HTML already contains a validated chart |
| **2** | `tests/test_scrape_chrome.py` | Chrome + Xvfb, VPS IP | JS-rendered chart after wait + cookie accept |
| **3** | `tests/test_scrape_tor.py` | Firefox via Tor SOCKS | Tier 2 failed; chart found via Tor |
| **4** | Login mimicry | Chrome + imported cookies | `LOGIN_REQUIRED` and chart found after session load |

**Escalation rule:** return data at the **first** tier where `deliverable_met == true`.

Implementation: `scrapers/tier_fallback.py`

---

## Two Selenium Tests

| Test file | Browser | Network | Tier |
|-----------|---------|---------|------|
| `tests/test_scrape_chrome.py` | Google Chrome | VPS public IP | 2 |
| `tests/test_scrape_tor.py` | Firefox + Tor | Tor exit IP | 3 |

Shared behavior:

- Require `REMOTE_SCRAPE=1`
- Fresh temp profile per site
- Accept cookies, wait for JS, scroll
- Wipe session data after each run
- Chrome asserts the chart; Tor may `pytest.skip` on bot block

```bash
export REMOTE_SCRAPE=1
export SCRAPE_ARTIFACTS_DIR=artifacts
export TOR_SOCKS=127.0.0.1:9050

xvfb-run -a pytest tests/test_scrape_chrome.py -m chrome -v
sudo systemctl start tor
xvfb-run -a pytest tests/test_scrape_tor.py -m tor -v
```

`python -m scrapers.run` re-execs under `xvfb-run` when `DISPLAY` is unset.

---

## Target Pages and Per-Site Scrape Contracts

Defined in `scrapers/contracts.py`:

| Site key | URL | Locale | Wait selectors | JS settle | Chart keywords |
|----------|-----|--------|----------------|-----------|----------------|
| `capitalone_venture_partners` | [Capital One venture partners](https://www.capitalone.com/learn-grow/money-management/venture-miles-transfer-partnerships/) | US EN | `shared-article-body`, `h1`, `main`, `table` | 3s | transfer, conversion, mile, point, ratio, award |
| `cathay_mega_miles_2026` | [Cathay Mega Miles 2026](https://www.cathaypacific.com/cx/en_US/offers/Mega-Miles-2026.html) | US EN | `h1`, `.c-accordion`, `main`, `table` | 4s | mile, point, conversion, earn, award, bonus |
| `jal_partner_point` | [JAL partner point](https://www.jal.co.jp/arl/en/sr/jalmile/partner/point/) | US EN | `#wrapper`, `#contents`, `dl`, `table` | 4s | point, mile, conversion, partner, award, ratio |

Each contract includes content keywords, block-title fragments, cookie-button labels, scroll, and `min_page_text_length`.

---

## Success / Failure Validation

Implemented in `scrapers/validation.py`.

| Outcome | Meaning | Action |
|---------|---------|--------|
| `success` | Chart found; deliverable met | Return data; ingest |
| `chart_not_found` | Page loaded, no chart above threshold | Escalate tier |
| `bot_blocked` | Akamai / Imperva / DataDome or generic block | Escalate tier; Tor may skip |
| `empty_content` | Text below `min_page_text_length` | Escalate; check Xvfb / waits |
| `wrong_region` | Geo redirect | Recreate VPS in another region or adjust URL |
| `login_required` | Gated markers without chart | Tier 4 |
| `timeout` | Selector / load timeout | Retry with backoff |
| `error` | Driver or network failure | Retry; log artifact |

**Deliverable gate:** `validation.deliverable_met` is `true` only when outcome is `success` and chart confidence ≥ 0.45.

---

## Bot Manager Detection

Checked in `scrapers/bot_detection.py` before chart validation.

| Vendor | Signals |
|--------|---------|
| **Akamai Bot Manager** | `_abck`, `bm_sz`, `ak_bmsc`, akamai references |
| **Imperva** | `incapsula`, `_incapsula_resource`, `visid_incap`, "Pardon Our Interruption" |
| **DataDome** | `datadome`, `geo.captcha-delivery.com`, `dd-cid`, `ddchallenge` |

Generic captcha / "unusual traffic" markers also fail as `bot_blocked`. Cookie names alone are not enough.

---

## Capture at Scrape Time

At Selenium capture (`scrapers/base_scraper.py`):

1. Accept cookies
2. Wait for contract selectors + JS settle
3. Scroll for lazy content
4. Save HTML, screenshot, visible text
5. Detect chart with BeautifulSoup + lxml
6. Write `{alias}_{tier}_{timestamp}.json`, `.html`, `.png` under `artifacts/` and per-site JSON under `test-results/`

---

## Architecture

```
┌──────────────────────── Ubuntu VPS ─────────────────────────────┐
│  REMOTE_SCRAPE=1                                                │
│  Tier 1 httpx ──► Tier 2 Chrome (VPS IP) ──► Tier 3 Tor (test)  │
│  fresh profile per site │ accept cookies │ wipe after session    │
│  xvfb-run (auto)                                                │
│  artifacts/ + Django DRF on 127.0.0.1:8000                     │
└────────────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
   VPS public IPv4                      Tor exit IP (experimental)
```

Laptop browsers are never used. Django does not drive Selenium.

---

## VPS Setup

### 1. Create the server

- **Image:** Ubuntu 22.04 or 24.04 **x86_64**
- **Size:** 2 vCPU / 4 GB RAM / 40 GB+ disk (Hetzner CX23)
- **Network:** public IPv4 (enable it on Hetzner; IPv6-only breaks many sites)
- **SSH key** login; do not open port 8000

### 2. Bootstrap

```bash
sudo bash scripts/vps_bootstrap.sh
# installs Chrome, Firefox (.deb, not snap), Tor, Xvfb, fonts, 2 GB swap
# sets timezone America/Los_Angeles
```

Copy `vm_profile.example.json` → `vm_profile.json` and `.env.example` → `.env`.

### 3. Environment (VPS only, not in git)

```bash
export REMOTE_SCRAPE=1
export SCRAPE_ARTIFACTS_DIR=artifacts
export SCRAPE_PROXY_TYPE=vps
export TOR_SOCKS=127.0.0.1:9050   # system tor, not Tor Browser
```

---

## Network Egress

| Path | Egress | Use |
|------|--------|-----|
| **Tier 2 Chrome** | VPS public IPv4 | Primary |
| **Tier 3 Tor** | Tor exit | Experimental probe |
| **Optional VPN** | Set `SCRAPE_PROXY_TYPE=vpn` if you add one | Not required |

One stable egress IP per page load. Log `egress_ip`, `proxy_type`, and `machine_id` in artifact JSON.

---

## Device Fingerprinting

Honest Linux signals only — do not spoof Windows.

| Signal | Approach |
|--------|----------|
| User-Agent | Real Chrome/Firefox binary (Tier 1 httpx uses the profile UA) |
| Accept-Language | `--lang=en-US` |
| Timezone | `timedatectl set-timezone America/Los_Angeles` |
| Screen | `--window-size=1920,1080` |
| Chrome flags | `--no-sandbox`, `--disable-dev-shm-usage`, `--disable-gpu` (required on typical VPS images) |

---

## Timezone

```bash
sudo timedatectl set-timezone America/Los_Angeles
sudo timedatectl set-ntp true
```

`scraped_at` is UTC in artifacts.

---

## Login Mimicry (Fallback Only)

Not used by default. Enable Tier 4 only when validation returns `LOGIN_REQUIRED`:

1. Log in once **on the VPS** in the same browser
2. Save cookies to `SESSION_COOKIE_PATH`
3. Re-run; Selenium injects cookies before the target URL

Do not import laptop session cookies.

---

## Session Isolation

| Rule | Detail |
|------|--------|
| `REMOTE_SCRAPE=1` | Required; blocks accidental laptop-profile use |
| Profile | New temp `--user-data-dir` / Firefox profile per site |
| Post-run | `delete_all_cookies()` → clear storage → `quit()` → delete profile dir |

---

## Speed, Memory, Redirects

| Technique | Action |
|-----------|--------|
| Tier 1 HTTP first | Skip browser if the chart is already in static HTML |
| `page_load_strategy = eager` | Do not wait for all subresources |
| Sequential default | One Chrome at a time on 4 GB |
| 2 GB swap | Bootstrap creates `/swapfile` |

Do **not** disable JavaScript. Do **not** run parallel Chrome workers on CX23.

`redirects.yaml` and `keywords.yaml` are unchanged. Inspect hits supplement the chart; they are not the deliverable gate.

---

## Operational Safeguards

| Safeguard | Implementation |
|-----------|----------------|
| Rate limit | ≥ 60s between requests to the same domain |
| Artifact logging | HTML, PNG, JSON with status, final URL, text length |
| `robots.txt` | Checked and recorded on each attempt |
| Fail loud | Chrome test asserts `deliverable_met`; Tor may skip |
| Export bind | Gunicorn on `127.0.0.1` only — DRF has no auth |
| Firewall | SSH only (`ufw` in bootstrap) |

---

## Django REST Framework

Runs on the VPS and does **not** drive Selenium.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/exports/` | All scrape exports |
| `GET` | `/api/exports/deliverables/` | Only rows where chart was found |
| `GET` | `/api/exports/{alias}/` | Single export with chart rows + inspect hits |
| `POST` | `/api/ingest/` | Load `artifacts/**/*.json` into DB |

```bash
python manage.py migrate
python manage.py ingest_artifacts
gunicorn --bind 127.0.0.1:8000 config.wsgi:application
curl http://127.0.0.1:8000/api/exports/deliverables/
```

`./scripts/scrape.sh` already migrates and ingests after the scrape.

---

## Running the Tests

**From-scratch VPS process (two Mac terminals, SSH keepalives, tmux, copy results home): see README.md.** Live scrapes write JSON under `test-results/<timestamp>_scrape/`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export REMOTE_SCRAPE=1
export SCRAPE_ARTIFACTS_DIR=artifacts
export TOR_SOCKS=127.0.0.1:9050

python -m scrapers.run --tier1-only
python -m scrapers.run --no-tor
python -m scrapers.run --site capitalone_venture_partners

xvfb-run -a pytest tests/test_scrape_chrome.py -m chrome -v
xvfb-run -a pytest tests/test_scrape_tor.py -m tor -v
```

Unit tests (no browser, safe on a laptop):

```bash
pytest tests/test_unit_core.py tests/test_unit_tier_fallback.py tests/test_unit_django_ingest.py -m unit -v
```

---

## One-Time Scrape Checklist

- [ ] Hetzner CX23 (x86) or other **4 GB** VPS created; IPv4 enabled
- [ ] `scripts/vps_bootstrap.sh` run
- [ ] `.env` and `vm_profile.json` in place; `DJANGO_SECRET_KEY` set
- [ ] `REMOTE_SCRAPE=1`
- [ ] `./scripts/scrape.sh` or `./scripts/scrape.sh --no-tor`
- [ ] `/api/exports/deliverables/` (localhost) has ≥ 1 chart, or failures documented
- [ ] Artifacts copied off the box if needed
- [ ] VPS deleted if it was hourly / one-shot

---

## Security and Legal Notes

1. **Terms of service** — Confirm automated access is permitted on each site.
2. **No secrets in git** — cookies and env stay on the VPS.
3. **One-time scope** — Single batch job; not continuous crawling.
4. **Tor** — Experimental probe; commercial sites often block Tor exits.
5. **Login mimicry** — Tier 4 only; never import laptop cookies.
6. **Datacenter IP** — Isolation from your laptop, not anonymity.
7. **Do not expose DRF** — no authentication on the export API.

---

## Quick Reference

| Item | Location |
|------|----------|
| Site contracts | `scrapers/contracts.py` |
| Chart detection | `scrapers/chart_detection.py` |
| Validation | `scrapers/validation.py` |
| Bot detection | `scrapers/bot_detection.py` |
| Tier fallback | `scrapers/tier_fallback.py` |
| Chrome (primary) | `tests/test_scrape_chrome.py` |
| Tor (experimental) | `tests/test_scrape_tor.py` |
| DRF export | `scrapes/views.py` |
| VPS bootstrap | `scripts/vps_bootstrap.sh` |

*Document version: 3.0 — single Ubuntu VPS; Chrome primary + Tor experimental; Django on localhost.*
