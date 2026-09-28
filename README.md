# Mileage

Credit-card points are worth about 1¢ in a bank travel portal and several cents when transferred to an airline partner and booked as a premium award. That gap is public information, scattered across partner charts, blog posts, and sites that block scrapers. **Mileage** takes a route, a cabin, and a points balance and says whether transferring beats the portal — and it will say “use the portal” when the transfer does not clearly win.

This repository is the full build, in the order it was built: early scrapers, the optimizer product, then a browser scrape for the pages plain HTTP cannot read.

| Piece | What it is |
|---|---|
| [`Test Scrapers/`](Test%20Scrapers/) | Experiments that picked the fetch strategy |
| [`mileage/`](mileage/) | The optimizer: scrape, verify, rank, CLI + API + React UI |
| [`Selenium Test/`](Selenium%20Test/) | Current scrape work: browser fallback for blocked partner pages |
| [`Selenium Test/IMPROVEMENTS.md`](Selenium%20Test/IMPROVEMENTS.md) | Next changes, from a live VPS run |

---

## 1. Test Scrapers

Three experiments. The longer write-up is [`Test Scrapers/PROJECT_SUMMARY.md`](Test%20Scrapers/PROJECT_SUMMARY.md).

### Broad crawler — `Test Scrapers/scraper`

A Playwright crawl of United and Capital One pages. Each page was read two ways: DOM parsing (tables, JSON-LD) and a local vision model (Ollama LLaVA) on a screenshot. Results landed in CSV.

It collected pages. It did not answer the product question. Most of a one-hour crawl was irrelevant URLs, vision was slow, and every extracted number was trusted. The useful data lives in a small set of known charts, with math on top (transfer, then redeem).

### Graph optimizer — `Test Scrapers/mini-scraper`

Currencies are nodes. Conversions are weighted edges. Value compounds by **multiplying** each hop.

Capital One does not transfer to United. A United seat goes through a Star Alliance partner (LifeMiles, Turkish, KrisFlyer, Aeroplan, ANA). Worked example: 1:1 into Turkish, a $200 flight at 7,500 miles → about **2.67¢** per Capital One mile.

```
path value = transfer ratio × (cash price in cents / miles required)
```

Playwright scrapers with a stealth fingerprint, plus vision as a fallback, fed a NetworkX graph and a SQLite store. The graph model held. Fetching did not. Airline bot walls (Akamai, Cloudflare) rejected the browser, and the LifeMiles chart never came back live.

### LifeMiles lab — `Test Scrapers/mini-scraper/lifemiles-lab`

Five ways to get one chart, scored on completeness, confidence, speed, and cost. No real priced rows meant a score of zero, so a hallucinated table could not win.

| Approach | What happened |
|---|---|
| Plain HTTP of a static aggregator chart | Winner. About 10 routes in 0.2s. No browser, no model. |
| Stealth browser on lifemiles.com | HTTP 403. The redeem page is a booking widget, not a chart. |
| Capture the page’s network calls | Same block. Prices only appear after a search. |
| Screenshot + local vision | On a block page, the model spent ~5 minutes and invented 44 routes. On a real chart image, ~5 minutes and zero usable rows. |
| Call LifeMiles endpoints directly | 403 on every endpoint. |

The fetch strategy that shipped next: request the published HTML chart, parse the table, and keep a browser only where the page actually needs one.

---

## 2. Mileage

`mileage/` is that product. Same question, checked answers, more than one bank currency (Capital One, Chase, Amex, Citi, Bilt).

```
route + cabin + points
        │
        ▼
  scrapers and fare sources
        │
        ▼
  verification (source, age, independent agreement)
        │
        ▼
  graph  →  portal only  |  roughly a tie  |  transfer wins
```

**Scraper.** HTTP first (`httpx`, optional browser-like TLS). HTML tables, JSON, RSS, and PDF. If a live page is blocked, fall back to a Wayback snapshot. Chart targets live in `mileage/knowledge/sources.yaml`.

**Verification.** A number enters the graph only when a parser actually hit page content. Two copies of the same published chart count as one source. Older data is trusted less. Newsletter and blog extraction is allowed only when the miles figure appears verbatim in the source text.

**Graph.** Transfer ratios, award charts (region, zone, and distance bands), and carrier surcharges. Surcharges depend on the currency **and** the airline operating the flight: the same Avios price on American metal and British Airways metal can differ by hundreds of dollars in cash.

**App.** CLI, FastAPI, and a Vite/React UI run one pipeline. Cache, rate limit, and lock are swappable (in-process, or Redis when several users share a route). OpenTelemetry traces are optional. Tests run offline; `mileage eval` feeds bad data (garbage values, missing sources, stale charts) and checks that none of it becomes the recommended route.

Rules the ranking keeps:

- “We did not check seats” and “we checked and found none” stay different.
- With no market cash fare, cents-per-point is blank.
- A route that needs a card you do not hold is shown with that card and its annual fee.

### Stack

Python 3.10+, FastAPI, NetworkX, SQLite, httpx, React/Vite, pytest. Optional: Redis, curl_cffi, OpenTelemetry.

### Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python -m mileage.cli demo
python -m mileage.cli quote --from LAX --to IST --cabin business \
    --currency capital_one --miles 90000 --card venture_x

pytest
python -m mileage.cli eval
```

API and UI:

```bash
uvicorn mileage.api.app:app --reload --port 8000
cd ui && npm install && npm run dev   # http://localhost:5173
```

The quote runs with no API keys. Amadeus (cash fares), seats.aero (live award space), and Arize (traces) turn on when their keys are set. Missing keys use a labeled fallback.

```bash
pip install -e ".[aggregator]"      # TLS impersonation, PDF/RSS
pip install -e ".[multiuser]"       # Redis
pip install -e ".[observability]"   # traces
```

---

## 3. Selenium Test

[`Selenium Test/`](Selenium%20Test/) opens the live pages — Capital One’s Venture transfer partners, Cathay Pacific’s miles-conversion offers, and JAL’s partner point chart — on a Hetzner CX23 (Ubuntu, 2 vCPU, 4 GB, billed hourly). Chrome needs about 4 GB, so a $5 / 1 GB droplet runs out of memory, and DigitalOcean or Linode only fit once you step up to their ~$24 4 GB plans. Selenium drives real Chrome and Firefox on that server through Xvfb, a virtual display, because the machine has no monitor. Each site walks a planned fallback and stops at the first method that returns a conversion chart: a plain HTTP fetch when the ratios are already in the HTML, then Selenium Chrome on the VPS IP for JavaScript and Akamai pages, then Firefox over Tor if Chrome is blocked, then a saved login session only if the page requires sign-in.

| Site | Page |
|---|---|
| Capital One | Venture miles transfer partners |
| Cathay Pacific | Miles conversion / partner offers |
| JAL | Partner point conversion |

Stop at the first tier that returns a real chart:

| Tier | How |
|---|---|
| 1 | HTTP (`httpx`). Enough when the ratios are already in the HTML. |
| 2 | Chrome on a virtual display (Xvfb). Primary path for JS pages. |
| 3 | Firefox through Tor. Experimental. |
| 4 | A saved login session, only if the page requires login. |

Each attempt writes HTML, a screenshot, and JSON under `test-results/` before the next site starts, so a crash keeps the earlier sites. A Django REST API on the same machine ingests those files and exports chart JSON. Laptop unit tests cover validation and ingest with no browser. Setup, SSH, and the runbook are in [`Selenium Test/README.md`](Selenium%20Test/README.md).

**Last VPS run**

| Site | Result |
|---|---|
| Capital One | HTTP succeeded. The partner ratios were in the HTML. |
| JAL | HTTP from the datacenter IP returned Access Denied. Chrome on that same IP returned the chart. |
| Cathay | The landing page was promo copy, not rates. Chrome followed one extra URL, that request failed (`ERR_HTTP2_PROTOCOL_ERROR`), and the run treated the hop as “no chart.” |

---

## 4. Next changes

From that run. Specified in [`Selenium Test/IMPROVEMENTS.md`](Selenium%20Test/IMPROVEMENTS.md). Not built yet.

**A chart is a success when it has rows.** Partner + rate (or a ratio list) means the scrape worked. No rows means it failed. The current 0.45 confidence score can reject a real chart (one landed at 0.33). Keep the score in the JSON for debugging. Stop using it as the pass/fail gate.

**Follow more than one link.** Landing page, then the partners or conversion page it points at, then nested tables. A failed hop (`ERR_HTTP2_*`, empty title) means try the next URL. Save HTML from every hop.

**Send browser-shaped HTTP, or skip HTTP.** Akamai on JAL and Cathay looks at header order, Client Hints, Accept-Language, and the HTTP/2 fingerprint. Default httpx headers do not match Chrome. Align them with the real browser profile, or skip Tier 1 on hosts that already 403. Log request headers and tracking cookie names (`_abck`, `bm_sz`, `ak_bmsc`). A tracking cookie plus a visible chart is still a chart.

**Use more than one browser.** Chrome is a single fingerprint. Firefox becomes its own tier, separate from Tor. Safari/WebKit is a later probe from a Mac; Safari does not run on the Ubuntu VPS. Rotate browser per site and per retry.

**Leave the datacenter IP.** One VPS address is one datacenter IP for the whole batch. Rotating residential proxies give each site (or each hop) a household IP, US-sticky for Capital One and Cathay. Log the egress IP. Rotate after Access Denied, an Akamai challenge, or an HTTP/2 protocol error. Tor stays a probe.

**Try SwiftChannel** as the session layer for proxy rotation, sticky sessions, and browser-aligned HTTP plus Selenium. Credentials stay in `.env` on the VPS.
