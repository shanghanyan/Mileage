# LifeMiles Scraping Lab

A tiny standalone harness that pits several extraction strategies against one
goal — **get the Avianca LifeMiles award chart** (origin → destination → miles
per cabin) — then scores them and promotes the winner into the mini-scraper's
row schema.

It exists because the production `lifemiles_scraper` keeps falling back to stale
cache. This lab isolates *why* and finds what actually works.

## TL;DR findings

| Strategy | What it does | Result |
|----------|--------------|--------|
| **`aggregator_html`** | httpx + BeautifulSoup over a static travel-site chart | ✅ **Winner** — 10 routes, ~0.2s, no browser, no model |
| `dom_table` | Stealth Playwright reads a `<table>` off lifemiles.com | ❌ Akamai 403 "Access Denied"; page is a JS booking widget with no chart |
| `network_intercept` | Stealth Playwright records XHR/fetch JSON | ❌ Akamai blocks the page; no payloads fire (prices need the search form) |
| `vision` | Screenshot + Ollama (`format=json`, model A/B) | ❌ blocked on lifemiles.com; impractically slow even on a clean chart image |
| `api_probe` | Direct httpx to lifemiles.com endpoints | ❌ Akamai 403 on every URL — proves a browser is mandatory |

### The three things this lab proved

1. **lifemiles.com is the wrong source.** It is fronted by Akamai (every direct
   request and even a stealth headless browser gets HTTP 403 "Access Denied"
   from `errors.edgesuite.net`), and the redeem URL is a JS booking widget that
   renders **no award chart** — so there is nothing for a selector *or* a vision
   model to read. LifeMiles also no longer publishes a complete machine-readable
   chart; real prices only come from running an individual route search.

2. **Vision is the wrong tool here — and it hallucinates.** Handed a screenshot
   of the "Access Denied" page, `llama3.2-vision:11b` spent **288s** confidently
   fabricating 44 fake routes ("New York → Los Angeles, 35,000 miles"…). Even
   given a clean, legible element-cropped screenshot of a *real* chart, local
   vision took **300s and still returned 0 usable rows**. The lab now:
   - **detects the Akamai block and skips vision entirely** (~2s) instead of
     trusting fabricated data (`common.detect_block`);
   - crops to the chart `<table>` element (full-page shots are unreadable once
     downscaled to the model's input);
   - forces valid JSON via Ollama `format=` (a strict schema or `"json"`),
     killing the production scraper's parse-failure retry loop;
   - hard-caps each model call with a wall-clock timeout so one slow generation
     can't hang the run;
   - benchmarks models A/B (`llava` vs `llama3.2-vision`) so the cost is visible.
   The scorer gives **0** to any strategy returning no *real* priced rows, so
   hallucinated/empty output can never win.

3. **A static aggregator chart is the reliable source.** `awardtravelfinder.com`
   (with `10xtravel.com` as fallback) publishes the current, post-devaluation
   region chart as a clean HTML table that returns 200 to a plain request and
   parses in a fifth of a second.

## Usage

```bash
cd mini-scraper/lifemiles-lab

# run every strategy and print the leaderboard
../venv/bin/python run_lab.py

# only the cheap/reliable ones (no browser, no Ollama)
../venv/bin/python run_lab.py --only aggregator_html,api_probe

# list strategies
../venv/bin/python run_lab.py --list

# vision: benchmark models, and (to validate the pipeline) point it at a page
# that actually renders a chart
../venv/bin/python run_lab.py --only vision \
  --vision-models llava:latest,llama3.2-vision:11b \
  --vision-chart-url https://awardtravelfinder.com/award-charts/lifemiles
```

Useful flags: `--headful` (watch the browser), `--nav-timeout`, `--http-timeout`,
`--ollama-host`, `--no-vision-schema` (use `format="json"` instead of a strict
JSON schema).

> **Sandbox note:** the browser strategies need a real Chromium. Inside
> restrictive sandboxes Chromium can `SIGABRT` on launch (environmental, not a
> bug) — run the lab from a normal shell.

## Output

Every run writes to `output/` (git-ignored):

- `<run_id>_report.md` / `_report.json` — leaderboard + per-strategy notes
- `lifemiles_rows.latest.json` — the winner's rows in the mini-scraper schema
  (`origin_zone`, `destination_zone`, `economy_miles`, `business_miles`,
  `first_miles`), ready to drop into `config/rates_cache.json`
- screenshots, captured API payloads, raw HTML, per-model vision JSON

Logs go to `logs/lab_<run_id>.log` (whole run) and `logs/strategy_<name>.log`.

## Scoring

`score = 45·completeness + 30·confidence + 15·speed + 10·cost` (0–100).
A strategy that returns no real priced rows scores 0. See `common.score_result`.

## How to fix the production scraper

Swap `lifemiles_scraper`'s vision-of-the-booking-page approach for the
`aggregator_html` strategy:

1. Fetch `awardtravelfinder.com/award-charts/lifemiles` with httpx (fallback to
   `10xtravel.com`), parse the chart table (see `strategies/s4_aggregator_html.py`).
2. Map the region labels to the existing zone names and emit the same `rows`
   shape (`run_lab.py` already does this — see `lifemiles_rows.latest.json`).
3. Keep vision only as a guarded fallback, and **never** run it against an
   Akamai-blocked page (use `common.detect_block`).

## Files

```
lifemiles-lab/
├── run_lab.py                     orchestrator: run / score / report / promote
├── common.py                      data model, parsing, stealth browser, scoring
├── strategies/
│   ├── s4_aggregator_html.py      static aggregator HTML  (winner)
│   ├── s1_dom_table.py            official-site DOM table scrape
│   ├── s2_network_intercept.py    Playwright XHR/API capture
│   ├── s3_vision.py               screenshot + Ollama, format=json, model A/B
│   └── s5_api_probe.py            direct httpx probe (documents the block)
├── logs/                          per-run + per-strategy logs
└── output/                        reports, promoted rows, artifacts
```
