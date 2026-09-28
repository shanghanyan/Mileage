# Pointy — Credit-Card Points → Flight Optimizer: Project Summary

This document is a complete, self-contained record of three related projects in this repository — **`scraper`**, **`mini-scraper`** (including its `lifemiles-lab`), and **`AggregateScraper`**. It captures each project's goal, architecture, the problems encountered, and the conclusions reached. It is written so that someone with no prior exposure could recreate any of the three from scratch using only this document.

---

## 0. The shared problem (why these projects exist)

Credit-card rewards "points" (Capital One miles, cashback, etc.) can be redeemed in many ways, and the value of a point varies wildly depending on **how** you redeem it. The same 1 Capital One mile might be worth 0.5¢ as a statement credit, 1.0–1.25¢ through a cash travel portal, or 3–6¢ if transferred to an airline partner and redeemed for a premium-cabin award seat.

Finding the best redemption is a full-time job done by paid experts, and the companies involved deliberately keep it opaque because they profit when people redeem sub-optimally. The thesis (see `Pointy Proposal.pdf`) is: **all of this information is public; the hard part is collecting it past anti-bot defenses and computing the optimal path.** An LLM/automation-assisted scraper plus a graph optimizer can democratize that expertise.

The canonical question all three projects try to answer:

> "I hold X Capital One miles / $Y cashback. I want to fly route O→D in cabin C. What is the single best way to convert my points into that seat, measured in **cents per point (CPP / CPM)**, and can I trust the number?"

### The one load-bearing domain fact

**Capital One does NOT transfer directly to United MileagePlus.** To reach a United (Star Alliance) flight you must transfer C1 miles to a Star Alliance partner program — **Avianca LifeMiles, Turkish Miles&Smiles, Singapore KrisFlyer, Air Canada Aeroplan, ANA Mileage Club** — and redeem the partner's award chart for a seat on United/partner metal. Every hop compounds.

### The three projects are an evolution of the same idea

| Project | Role | Fetch strategy | Maturity |
|---------|------|----------------|----------|
| **`scraper`** | First pilot: a generic broad-crawl "travel intelligence" scraper | Playwright (headed) + Ollama LLaVA vision, DOM+vision merge | Proof of concept |
| **`mini-scraper`** | Focused Capital One→United optimizer with a real graph | Per-site Playwright stealth scrapers + vision fallback | Working prototype |
| **`AggregateScraper`** | Production-grade rewrite of mini-scraper | **httpx + BeautifulSoup first** (no browser/LLM for core), cross-checked, freshness-aware | Most mature |
| `mini-scraper/lifemiles-lab` | A scientific side-experiment | A/B harness pitting 5 extraction strategies | Diagnostic tool |

The arc is: **broad LLM-vision crawl → focused graph optimizer with vision → lightweight verified HTTP scraper**. The central lesson driving that arc (proven in `lifemiles-lab`) is that **local vision models were the wrong tool**, and a plain HTTP fetch of a static aggregator chart is faster, cheaper, and more reliable.

---

## 1. Project: `scraper` (the generic pilot crawler)

### 1.1 Goal

A general-purpose, local "Travel Intelligence Scraper." Given a set of seed URLs and a domain scope, it crawls breadth-first, extracts any structured travel/loyalty data it can find from each page using **two independent extraction modes** (DOM parsing + LLM vision), merges them, and writes a flat CSV for downstream analysis. The pilot scope was United Airlines, United credit cards, and Capital One credit cards.

It is deliberately **dumb about domain semantics** — it does not know about CPP or graphs. It is a data-collection layer: visit pages, pull out tables/JSON-LD/prices, dump to CSV. It is also **self-limiting**: it stops after 1 hour and writes a partial CSV, and it checkpoints so it can resume.

### 1.2 Architecture

```
scraper/
├── scraper.py            # orchestrator: BFS queue, checkpointing, timing, logging
├── config.yaml           # seeds, scope (per-domain depth/page caps), rate limits, blocklist
├── crawl/
│   ├── browser.py        # Playwright BrowserSession (launch, load_page, random_delay)
│   └── scope.py          # ScopeManager: domain/depth/page caps, blocklist, sitemap discovery, URL normalize
├── extraction/
│   ├── dom.py            # Mode 1: BeautifulSoup tables + JSON-LD + meta + accessibility tree text
│   ├── vision.py         # Mode 2: screenshot → Ollama LLaVA → structured JSON
│   ├── prompts.py        # vision prompt(s)
│   └── merge.py          # merge vision+DOM into records; records_from_merged()
├── storage/
│   ├── sqlite_store.py   # raw page visits + extracted records
│   └── csv_export.py     # flatten records → output.csv
├── setup.sh              # venv + pip + playwright install chromium + ollama check
└── requirements.txt      # playwright, bs4, lxml, pandas, requests, ollama, pyyaml, apscheduler
```

**Control flow (`scraper.py`):**

1. `resolve_dirs()` picks `screenshots/` and `data/` folders (defaults next to the script).
2. `setup_logging()` configures console + a `TimedRotatingFileHandler` (`data/scraper_YYYY-MM-DD.log`, rotated at midnight, 7 backups).
3. `load_config()` reads `config.yaml`.
4. `ScraperOrchestrator` is constructed: builds `ScopeManager`, `BrowserSession`, `SQLiteStore`, and a `VisionExtractor`. It probes Ollama/LLaVA at startup via `VisionExtractor.check_ollama()` — if Ollama is down, vision is disabled and the crawl continues **DOM-only** (graceful degradation).
5. `run()`:
   - Loads a `checkpoint.json` if present (resume), else seeds the queue from `config.seeds` and optionally discovers sitemap URLs (`add_sitemap_seeds`).
   - BFS loop with a wall-clock guard (`max_runtime_seconds`, default 3600). For each URL: scope check (`can_visit`), randomized rate-limit delay (slower for "aggregator" domains), `scrape_page()`, persist, enqueue same-scope child links, and **save a checkpoint every page**.
   - `stop_reason` ∈ `complete | timeout | interrupt`.
6. `scrape_page()` loads the page, runs `extract_dom()` and `vision.extract()` independently, then `merge_extractions()`. A page only counts as failed if **both** modes fail; failures stream to `failed_urls.log`. A domain that fails 10 times in a row logs a `[DOMAIN_WARN]`.
7. `shutdown()` exports SQLite → `output.csv`, removes the checkpoint only on clean completion (keeps it otherwise so re-running resumes), and prints a crawl summary.

**Two-mode extraction (the core idea):**

- **Mode 1 — DOM (`extraction/dom.py`):** parse `<table>`s, `application/ld+json` blocks, `<meta>` tags, and a flattened Playwright **accessibility tree** into text. Deterministic, fast, no model.
- **Mode 2 — Vision (`extraction/vision.py`):** full-page screenshot → Ollama LLaVA with a JSON-coercing prompt → structured fields. Intended as a fallback for JS-rendered pages with no clean DOM.
- `merge.py` reconciles the two into typed records (`records_from_merged`) tagged with which mode(s) succeeded.

**Outputs** (`data/`): `output.csv` (main deliverable), `united_scraper.sqlite`, `visit_log.jsonl` (one line/page), `failed_urls.log`, `skipped_urls.log`, plus retained screenshots in `screenshots/`.

### 1.3 Issues

- **Headed browser + slow_mo:** default config runs **visible** Chromium (`headless: false`, `browser_slow_mo_ms: 80`) — fine for a human-watched pilot, impractical for automation.
- **Vision is heavy and unreliable:** LLaVA calls are slow (30s timeout each) and the broad "describe this page" approach yields noisy/low-signal output on most pages. This foreshadowed the later vision findings.
- **No verification layer:** any extracted value is trusted and written to CSV. No cross-checking, no freshness, no confidence — it is a collector, not a judge.
- **Bot blocking:** real loyalty sites (United, Capital One) actively fight automation; a generic crawler with one fingerprint gets blocked or fed JS-only shells with nothing for the DOM parser to read.
- **Breadth over precision:** crawling whole domains to depth 3–4 spends most of the hour on irrelevant pages (careers, legal) despite the blocklist.

### 1.4 Conclusions

A broad crawl + dual-mode extraction is a reasonable *generic* tool, but for this domain it is the wrong shape: the valuable data lives in a **small, known set of charts**, not somewhere across an entire airline domain, and the semantics (compounding transfer/redemption math) matter more than raw collection. This directly motivated `mini-scraper`: replace the broad crawler with a handful of **targeted per-program scrapers** feeding a **graph optimizer** that actually computes CPP. Vision was kept, but demoted to a fallback.

---

## 2. Project: `mini-scraper` (focused graph optimizer)

### 2.1 Goal

Scrape live transfer ratios, Star Alliance award charts, and third-party point valuations, then run a **weighted-graph optimizer** to rank every redemption path from a user's holdings to USD by **cents per Capital One mile (CPP)**. Unlike `scraper`, this project *understands the domain*: it models points programs as a graph and computes compounding value.

`CURSOR_INSTRUCTIONS.md` is the authoritative build spec and declares the schema and CPP formula "load-bearing" — they must not change without updating every scraper's `to_edges()` simultaneously.

### 2.2 The model: points as a weighted directed graph

Every currency is a **node**; every conversion is a **directed edge** carrying an `edge_cpp` weight. `USD` is the terminal sink.

Nodes (`optimizer/models.py`, `Currency` enum): `C1_MILES`, `C1_CASHBACK` (units = USD), `LIFEMILES`, `TURKISH_MILES`, `KRISFLYER`, `AEROPLAN`, `USD`.

**The CPP formula (Section 8 of the spec) — compound by PRODUCT, not sum:**

```
path_cpp = edge_cpp_1 × edge_cpp_2 × … × edge_cpp_n
```

| Edge type | `edge_cpp` value |
|-----------|------------------|
| Transfer (C1 → partner) | the transfer ratio (partner units per 1 input unit), e.g. 1.0 for 1:1 |
| Redemption (partner → USD) | `cash_price_cents / miles_needed` |
| Direct cash (C1 → USD) | the CPP directly: 0.5 (statement credit), 0.8 (gift cards), 1.0 (travel portal) |

**Worked example (Turkish domestic economy):** C1→Turkish ratio = 1.0; a $200 flight costs 7,500 Turkish miles → `1.0 × (20000 / 7500) ≈ 2.667¢` per C1 mile.

Special edges (`optimizer/graph.py::get_static_edges`):
- `C1_CASHBACK → USD` with `edge_cpp = 100.0` (because cashback units are dollars; $1 = 100¢ face value).
- `C1_CASHBACK → C1_MILES` is `is_one_way=True` (irreversible) — cashback can become miles but never back.

### 2.3 Architecture

```
mini-scraper/
├── main.py                 # async orchestrator: run scrapers → upsert edges → optimize → render
├── config/
│   ├── scraper_config.yaml # per-site URLs, timeouts, screenshot crops, wait selectors
│   └── rates_cache.json    # last verified scraped rows (human-readable snapshot + fallback)
├── scrapers/
│   ├── base_scraper.py     # BaseScraper ABC, stealth Playwright helpers, retry, CACHED fallback
│   ├── vision_scraper.py   # Ollama vision (screenshot crop → JSON), model fallback chain
│   ├── capital_one_scraper.py, lifemiles_scraper.py, turkish_scraper.py,
│   ├── krisflyer_scraper.py, aeroplan_scraper.py            # award/transfer sources
│   ├── tpg_scraper.py, nerdwallet_scraper.py                # third-party VALUATIONS (sanity only)
│   ├── awardwallet_scraper.py                               # devaluation alerts + articles
│   └── awardfares_scraper.py                                # aggregate "sweet spot" mileage
├── optimizer/
│   ├── models.py           # Pydantic: Currency, TransferEdge, RedemptionPath, UserPortfolio, …
│   ├── graph.py            # build_graph() → nx.MultiDiGraph; get_static_edges()
│   └── optimizer.py        # PointOptimizer: enumerate paths, rank, dedupe, value, render
├── storage/database.py     # aiosqlite: edges, runs, devaluation alerts (source of truth)
├── lifemiles-lab/          # standalone extraction-strategy A/B harness (see §4)
├── logs/                   # per-scraper logs + optimization_result_*.json
└── run.sh / setup.sh
```

**Orchestration (`main.py`):**

1. Build a `FAKE_PORTFOLIO` (demo: 85k C1 miles, $200 cashback, 15k Turkish miles — user edits this).
2. Run scrapers **in a fixed order** (`SCRAPER_ORDER`), Capital One and AwardWallet first, under a wall-clock budget (`SCRAPE_MAX_MINUTES`, default 60).
3. Each scraper returns a `ScraperResult` with status `SUCCESS | CACHED | RETRYING | FAILED | SKIPPED`. On `SUCCESS`/`CACHED` with data, `scraper.to_edges()` converts the payload into `TransferEdge` objects.
4. AwardWallet articles flagged `is_alert` trigger `db.flag_stale()` + a saved devaluation alert.
5. `get_static_edges()` adds the C1→USD cash edges. All edges are **upserted into SQLite**, which is then the **source of truth** — the optimizer reads edges back out of the DB (`db.load_edges()`).
6. `build_graph(edges)` → `nx.MultiDiGraph` (multi-edge so parallel C1→USD redemptions coexist).
7. `PointOptimizer.find_all_paths()` enumerates, ranks, and dedupes paths; `value_portfolio()` values holdings; results render as Rich tables and save to `logs/optimization_result_*.json`; the run is persisted with `db.save_run()`.

**The optimizer (`optimizer/optimizer.py`) — the interesting algorithm:**

- For each source currency present in the graph, enumerate `nx.all_simple_paths(G, src, USD, cutoff=4)`.
- Because it is a `MultiDiGraph`, each node-path may have multiple edge combinations; `_edge_combinations()` takes the Cartesian product of parallel edges per hop.
- `_build_path()` multiplies `edge_cpp` across the combo to get `total_cpp`, and propagates `is_one_way`, `has_stale`, `has_suspicious` up to the path.
- **Ranking (`_rank_key`):** sort reversible paths ahead of one-way paths, then by penalty-adjusted CPP descending. One-way (cashback→miles) paths are multiplied by `ONE_WAY_PENALTY` (default 0.5) so an irreversible conversion never monopolizes the top despite an inflated face CPP.
- **Dedup (`_path_key` / `_normalize_ratio_str`):** different scrapers emit the *same* economic edge with cosmetically different labels ("1:1" vs "1.00:1"). Paths are deduped on a canonical key (currency chain + rounded per-hop `edge_cpp` + ratio-normalized labels) so the leaderboard shows genuinely distinct routes.
- A path is only marked `recommended=True` if it is the top path **and** `has_stale=False` (spec rule 6: never recommend stale data).

**Verification / sanity (kept *out* of the graph):**
- TPG and NerdWallet valuations are **not edges**. They are reference points: `sanity_check_tpg()` warns if a computed CPP exceeds the TPG valuation by >40%.
- AwardFares' single aggregate "sweet spot" mileage is compared against the nearest comparable LifeMiles chart route, emitting **at most one** warning (an early version produced a "warning storm" — one per long-haul path).
- `_validate_cpp_baselines()` warns when a path labeled statement-credit/gift-card/portal deviates >30% from its known baseline (0.5/0.8/1.0).

**Scraper resilience (`base_scraper.py`):** every Playwright scraper gets a **stealth fingerprint** — rotating realistic user agents, `--disable-blink-features=AutomationControlled`, an init script that patches `navigator.webdriver/languages/plugins`, realistic context headers/locale/timezone. Navigation is resilient (`goto_resilient` tries `domcontentloaded → load → commit`; `wait_for_selector_safe` never raises). `scrape()` wraps `_fetch()` in `tenacity` exponential-backoff retries (3 attempts); on total failure it falls back to `rates_cache.json` and returns status `CACHED`.

### 2.4 Issues

- **Stealth ≠ invincible:** Capital One, TPG, and Turkish reject the default Playwright fingerprint; the stealth layer was added precisely because scrapers kept hard-failing into the stale-cache path. It helps but does not beat enterprise WAFs (Akamai/Cloudflare).
- **The LifeMiles scraper kept falling back to stale cache** — it could never get a live chart. This was important enough to spawn a dedicated diagnostic lab (§4).
- **Vision was slow, fragile, and hallucinated** (quantified in the lab): hundreds of seconds per call, JSON parse failures, and fabricated routes when handed a block page.
- **One-way and duplicate paths polluted rankings** until the `ONE_WAY_PENALTY` and canonical-dedup logic were added.
- **Warning storms:** naive sanity checks fired one warning per route; had to be collapsed to a single representative warning.
- **Heavy dependency stack:** Playwright + Chromium + Ollama + a vision model is a large, environment-sensitive install (Python 3.13 breaks pinned `playwright`/`pydantic` wheels; Chromium `SIGABRT`s in some sandboxes).

### 2.5 Conclusions

The graph model is the right abstraction and survives into `AggregateScraper` essentially unchanged (compound-by-product CPP, currencies-as-nodes, edges with provenance, one-way penalties, stale/suspicious flags). The **fetch strategy was wrong**: Playwright + local vision was heavy and unreliable, and for the worst offender (LifeMiles) it never worked at all. The lab proved a plain HTTP fetch of a static aggregator chart beats it on every axis, which set the direction for the production rewrite.

---

## 3. `mini-scraper/lifemiles-lab` (the decisive experiment)

### 3.1 Goal

An isolated, scientific harness to answer one question: **what is the most reliable way to obtain the Avianca LifeMiles award chart** (origin → destination → miles per cabin)? It exists because the production `lifemiles_scraper` kept falling back to stale cache. The lab runs several extraction strategies head-to-head, **scores** them, and **promotes** the winner's rows into the mini-scraper schema.

### 3.2 Architecture

```
lifemiles-lab/
├── run_lab.py              # orchestrator: run → score → report → promote winner
├── common.py               # data model, parsing, stealth browser, block detection, scoring
├── strategies/
│   ├── s1_dom_table.py        # stealth Playwright reads a <table> off lifemiles.com
│   ├── s2_network_intercept.py# Playwright records XHR/fetch JSON payloads
│   ├── s3_vision.py           # screenshot crop → Ollama, format=json, model A/B benchmark
│   ├── s4_aggregator_html.py  # httpx + BeautifulSoup over a static aggregator chart  ← WINNER
│   └── s5_api_probe.py        # direct httpx to lifemiles.com endpoints (documents the block)
├── logs/                   # per-run + per-strategy logs
└── output/                 # reports (.md/.json), promoted rows, screenshots, payloads
```

**Scoring:** `score = 45·completeness + 30·confidence + 15·speed + 10·cost` (0–100). Any strategy returning **no real priced rows scores 0**, so hallucinated or empty output can never win. Block detection (`common.detect_block`) short-circuits doomed strategies in ~2s instead of trusting fabricated data.

### 3.3 Findings (the three things the lab proved)

| Strategy | Result |
|----------|--------|
| `aggregator_html` (httpx + BS4 over awardtravelfinder.com, 10xtravel.com fallback) | ✅ **Winner** — 10 routes in ~0.2s, no browser, no model |
| `dom_table` (stealth Playwright on lifemiles.com) | ❌ Akamai 403 "Access Denied"; page is a JS booking widget with no chart |
| `network_intercept` (Playwright XHR capture) | ❌ Akamai blocks the page; prices only fire from a search form |
| `vision` (screenshot + Ollama) | ❌ blocked on site; impractically slow even on a clean chart image |
| `api_probe` (direct httpx) | ❌ Akamai 403 on every endpoint — proves a browser is "mandatory" yet still blocked |

1. **lifemiles.com is the wrong source.** It is fronted by Akamai (every direct request *and* a stealth headless browser get HTTP 403 from `errors.edgesuite.net`), and the redeem URL is a JS booking widget that renders **no chart** — nothing for a selector *or* a vision model to read. LifeMiles also no longer publishes a complete machine-readable chart.

2. **Vision is the wrong tool — and it hallucinates.** Handed a screenshot of the "Access Denied" page, `llama3.2-vision:11b` spent **288s confidently fabricating 44 fake routes**. Even given a clean cropped screenshot of a *real* chart, local vision took **~300s and returned 0 usable rows**. Mitigations adopted: detect the Akamai block and skip vision entirely; crop to the chart element; force valid JSON via Ollama `format=`; hard-cap each call with a wall-clock timeout; benchmark models A/B so cost is visible.

3. **A static aggregator chart is the reliable source.** `awardtravelfinder.com` (fallback `10xtravel.com`) publishes the current post-devaluation region chart as a clean HTML table that returns 200 to a plain request and parses in a fifth of a second.

### 3.4 Conclusion → the prescription that became `AggregateScraper`

> Replace the vision-of-the-booking-page approach with the `aggregator_html` strategy: fetch the aggregator chart with httpx, parse the table, map region labels to zone names, emit the standard row shape. Keep vision only as a guarded fallback and **never** run it against a blocked page.

This is exactly the philosophy `AggregateScraper` is built on.

---

## 4. Project: `AggregateScraper` (production-grade rewrite)

### 4.1 Goal

The same question as `mini-scraper`, answered honestly and reliably for a **specific route query**. Given `--origin --dest --cabin --cash [--fees]`, rank every verified path from Capital One miles to a United/Star-Alliance seat by **cents per mile**, comparing two fundamentally different routes and **refusing to declare a winner without verified data on both sides**.

Guiding principles (from `README.md`):
- **No hallucinations** — data enters the graph only from scraped sources with an actual selector hit.
- **Cross-check** — two independent sources must agree (Capital One is authoritative for transfer ratios).
- **Live precedence** — seats.aero live mileage overrides static zone charts when available.
- **Honest conclusions** — never name a winner unless verified data exists on both the portal and transfer paths.

### 4.2 The two competing paths

| Path | Flow | Floor |
|------|------|-------|
| **A — Portal** | C1 → Capital One Travel → United **cash** fare | 1.0¢ (Venture) / 1.25¢ (Venture X), set by `PORTAL_CPP` |
| **B — Transfer** | C1 → Star Alliance partner → United **award** seat | Must beat the portal by **≥20%** (`WIN_MARGIN`) to be recommended |

### 4.3 Architecture

```
AggregateScraper/
├── main.py                 # CLI (argparse): --origin/--dest/--cabin/--cash/--fees,
│                           #   --offline, --refresh-all, --check-freshness, --check-alerts,
│                           #   --validate-urls, --nonstop-only, --show-all
├── config/
│   ├── partners.yaml       # 22 C1 transfer partners: ratios, alliance, carriers, routing, surcharges
│   ├── scrape_targets.yaml # per-program ordered URL chains + parser type
│   ├── rss_feeds.yaml      # RSS/Atom award feeds polled in parallel (bot-block workaround)
│   ├── sources.yaml        # legacy URLs, devaluation feeds, bot block_signatures
│   ├── zone_mapping.yaml   # airport IATA → each program's zone names
│   └── rates_cache.json    # last verified scraped rows (timestamped per dataset)
├── data/fallback_rates.json# last-known-good rates when scrapers fail
├── scrapers/               # httpx + BS4 fetchers → ScrapedRow (no Playwright/Ollama for core)
│   ├── base.py             # ScrapedRow dataclass (the universal contract) + parse_miles_text
│   ├── http_util.py        # resilient fetch, bot-block detection, retry/backoff
│   ├── wayback.py          # archive.org fallback for bot-blocked live pages
│   ├── chart_scraper.py    # drives scrape_targets.yaml: live → wayback → pdf → RSS per program
│   ├── c1_transfer_partners.py, awardtravelfinder.py, blog_parsers.py, pdf_chart.py,
│   ├── rss_awards.py, rss_monitor.py, seats_aero.py, ana_pricing.py, freshness_extract.py,
│   ├── validate_urls.py, scraper_logger.py, exceptions.py
├── verify/                 # the trust layer
│   ├── cross_check.py      # consensus across sources; authoritative-source short-circuit
│   ├── trust.py            # age-based trust score + weighted-median consensus
│   ├── freshness.py        # decay confidence with data age
│   └── bounds.py           # sanity bounds on CPP per cabin
├── graph/                  # NetworkX DiGraph + path ranker
│   ├── edges.py            # TransferEdge / AwardEdge / PortalEdge
│   ├── builder.py          # build_graph(): zone-specific award nodes per cabin
│   ├── optimizer.py        # rank_paths() + conclude_winner() (the 20% margin rule)
│   ├── partners.py         # partner metadata, relevance filtering, c1_miles_required()
│   ├── zones.py, carriers.py
├── pipeline/
│   ├── orchestrator.py     # scrape → verify → graph → optimize → conclude; cache scheduling
│   └── fallback.py         # fallback_rows_for_route()
├── display/rich_table.py   # Rich leaderboard, freshness report, alerts
├── db/store.py             # SQLite: edges, runs, alerts
├── tests/                  # test_hardening, test_regression_june18, test_remediation
└── .github/workflows/url_health.yml  # monthly HEAD-check of all scrape targets
```

**Dependency contrast — this is the headline change:** `requirements.txt` is **httpx, beautifulsoup4, lxml, PyYAML, networkx, rich, python-dotenv, feedparser, pytest, pdfplumber, pypdf**. There is **no Playwright and no Ollama**. The lab's conclusion is baked into the dependency list.

**The universal data contract (`scrapers/base.py::ScrapedRow`):** a single dataclass every source emits — `source_name/url`, `scraped_at`, `from_program`, `to_program`, optional `transfer_ratio`, optional zones + `economy/business/first_miles`, `raw_cell_text`, `selector_matched`, `confidence`, `flags`, `source_updated_at`, `source_trust`, `source_count`, `miles_range_low/high`. `is_usable()` enforces a key integrity rule: **no selector hit → not usable**, and rows flagged `hallucinated` are never usable.

**Resilient fetching (`http_util.py` + `chart_scraper.py` + `wayback.py`):**
- `scrape_targets.yaml` gives each program an **ordered list of targets**, each with a `parser` (`blog_table`, `blog_prose`, `awardtravelfinder`, `pdf_chart`) and optional `fetch_via`.
- `fetch_html_resilient()` tries live HTTP with exponential backoff; `is_bot_blocked()` detects 403/429 and signatures (Cloudflare, Akamai, "Access denied", "Just a moment…"). On a block it **falls back to a Wayback Machine snapshot** (archive.org), tracking the snapshot date as `source_updated_at`.
- PDFs are parsed with `pdfplumber`/`pypdf`. RSS/Atom award feeds (`rss_awards.py`) are polled in parallel as an additional bot-block workaround.
- ANA charts are round-trip; `ana_pricing.get_ow_miles()` normalizes RT→OW and flags `rt_to_ow_normalized`.

**The verification layer (the biggest addition over mini-scraper):**
- `verify/trust.py::compute_trust()` maps data age to a trust weight (≤30d → 1.0, ≤60d → 0.85, ≤120d → 0.65, … >365d → 0.10).
- `verify/cross_check.py::cross_check()` decides whether a group of rows for the same (program, zones) becomes a graph edge:
  - If an **authoritative** source (`capitalone.com`, `partners.yaml`) is present and usable, it wins outright.
  - A single usable source → `medium`/`low` confidence + `single_source` flag.
  - Multiple sources → `consensus_rate()` computes a **trust-weighted median**; if the spread exceeds a 10% tolerance, the edge is flagged `sources_disagree_NN%` and demoted to `low` confidence.
  - A lone `fallback_rates.json` row is flagged `hardcoded_fallback`.
- `verify/freshness.py` decays confidence as data ages; `verify/bounds.py` sanity-checks CPP per cabin.

**Graph + ranking (`graph/`):** `build_graph()` builds a `nx.DiGraph` with a `capital_one → usd_portal` portal edge, transfer edges (only `high/medium/low` confidence), and **zone- and cabin-specific award nodes** (`{program}_{cabin}_{originSlug}_{destSlug}`); unverified award edges are dropped. `rank_paths()` computes, for the queried route, CPP for the portal and each relevant partner via `compute_cpp(effective_ratio, partner_miles, cash, fees, surcharge)` = `(net_value × 100) / c1_miles_needed`. It honors seats.aero **live miles** (overriding chart miles, adding a `✓ live` flag), shows a CPP **range** when sources disagree, applies carrier surcharges, and filters partners by route relevance (`--nonstop-only` hides connecting itineraries).

**The conclusion engine (`conclude_winner`) — the "honesty" core:**
- No verified, non-stale transfer paths → verdict `portal_only` ("portal is your confirmed floor").
- Best transfer within `WIN_MARGIN` (20%) of portal → verdict `comparable` ("weigh availability/fees").
- Best transfer beats portal by ≥20% → `best`, or `tentative_best` ("pending verification") if the winner carries any warning flags.

**Cache scheduling (`pipeline/orchestrator.py`):** the orchestrator only re-scrapes datasets older than their per-type schedule (transfer partners / award charts, default 7 days), honors `--offline` (cache only) and `--refresh-all`, and merges in `fallback_rates.json` when a program has no fresh data — always surfacing age via `--check-freshness`.

### 4.4 Issues

- **Bot blocking is the central, recurring adversary.** The entire `http_util`/`wayback`/RSS/PDF stack and `config/sources.yaml` block signatures exist to route around Cloudflare/Akamai. This is never fully "solved"; it is mitigated with fallbacks.
- **Round-trip vs one-way mile charts** (ANA) silently doubled award costs until `get_ow_miles()` normalization + flagging was added.
- **Freshness reporting duplicated rows** — `_dedupe_freshness_entries()` collapses one entry per (program, source, scraped_at) instead of one per parsed chart row (regression fixed June 18, covered by `tests/test_regression_june18.py`).
- **Distinguishing failure modes:** Wayback rate-limits vs missing snapshots needed distinct exceptions (`RateLimitError` vs `WaybackSnapshotMissingError`) so retries behave correctly.
- **URL rot:** blog/aggregator URLs change or 404; hence `--validate-urls`, per-target `last_404` tracking, and a monthly GitHub Actions health check.
- **Sparse data for a given route:** many (program, zone, cabin) combinations simply have no current public chart, so the system must degrade to fallback rates and say so rather than guess.

### 4.5 Conclusions

`AggregateScraper` is the realized thesis: a **lightweight, verifiable, honest** optimizer. The architecture cleanly separates concerns — `scrapers/` (get raw `ScrapedRow`s, any source) → `verify/` (decide what is trustworthy) → `graph/` (compute CPP) → `pipeline/` (orchestrate + cache) → `display/` (present). The dominant engineering reality is that **the data, not the math, is the hard part**: anti-bot defenses, stale/round-trip/missing charts, and source disagreement drive most of the code. The product's integrity comes from refusing to promote unverified data into the graph and from a conclusion engine that would rather say "portal is your floor" than name an unverifiable winner.

---

## 5. Cross-cutting lessons (the through-line)

1. **Match the tool to the data.** Broad LLM-vision crawling lost to targeted scraping, which lost (for fetching) to plain HTTP of static aggregator pages. Vision was demoted from primary (scraper) → fallback (mini-scraper) → removed from core (AggregateScraper).
2. **Local vision models hallucinate and are slow.** Empirically: 288s to fabricate 44 fake routes from a block page; 300s for 0 rows from a real chart. Never trust an unconstrained vision model with quantitative extraction; if used at all, force JSON, crop tightly, time-box, and score 0 for empty/fake output.
3. **Anti-bot defenses dominate the engineering.** Akamai/Cloudflare 403 everything, including stealth headless browsers. The durable answer is to **change source** (third-party aggregators, RSS, Wayback snapshots, PDFs) rather than out-fingerprint a WAF.
4. **Provenance and verification are first-class.** Every datum carries its source, timestamp, and trust; nothing enters the graph without a selector hit; independent sources must agree; age decays confidence.
5. **The graph/CPP model is stable and correct.** Currencies as nodes, conversions as weighted edges, CPP compounding by product, one-way penalties, stale/suspicious propagation — invented in mini-scraper, kept in AggregateScraper.
6. **Be honest about uncertainty.** Surface stale/disagreement/fallback flags, show CPP ranges, and refuse to name a winner without verified data on both sides.

---

## 6. How to recreate (from zero)

### 6.1 Domain constants you must encode
- **No C1 → United MileagePlus direct transfer.** Route via Star Alliance partners (LifeMiles, Turkish, KrisFlyer, Aeroplan, ANA).
- **CPP compounds by product**: `path_cpp = ∏ edge_cpp`. Transfer edge → ratio; redemption edge → `cash_cents / miles`; direct cash → 0.5/0.8/1.0.
- **C1 cashback is denominated in USD** ($1 = 100¢, so its face-value edge_cpp = 100); cashback→miles is **one-way**.
- **Portal floor** = 1.0¢ (Venture) or 1.25¢ (Venture X). A transfer must beat it by **≥20%** to win.
- **ANA charts are round-trip** — halve to one-way.

### 6.2 Minimal recreate of `AggregateScraper` (recommended target)
Build order:
1. `scrapers/base.py` — the `ScrapedRow` dataclass + `parse_miles_text()` (handles "90K", "90,000", "90000").
2. `scrapers/http_util.py` — `fetch_html` with backoff + `is_bot_blocked()` (403/429 + signature list); `wayback.py` for archive.org fallback.
3. `config/*.yaml` — `partners.yaml` (partner ratios/metadata), `scrape_targets.yaml` (ordered URL+parser per program; **prefer static aggregator charts like awardtravelfinder.com**), `zone_mapping.yaml`, `rss_feeds.yaml`, `sources.yaml`; plus `data/fallback_rates.json`.
4. `scrapers/chart_scraper.py` + parsers (`awardtravelfinder.py`, `blog_parsers.py`, `pdf_chart.py`, `rss_awards.py`) — fetch → parse → `ScrapedRow`.
5. `verify/trust.py` (age→trust, weighted-median consensus), `verify/cross_check.py` (authoritative short-circuit, consensus, disagreement flags), `verify/freshness.py`, `verify/bounds.py`.
6. `graph/edges.py`, `graph/partners.py` (incl. `c1_miles_required`), `graph/zones.py`, `graph/builder.py`, `graph/optimizer.py` (`compute_cpp`, `rank_paths`, `conclude_winner` with the 20% margin).
7. `pipeline/orchestrator.py` (cache scheduling + scrape→verify→graph→optimize→conclude), `pipeline/fallback.py`.
8. `db/store.py` (SQLite edges/runs/alerts), `display/rich_table.py`, `main.py` (argparse CLI).
9. Optional: `scrapers/seats_aero.py` (live availability override, gated on `SEATS_AERO_API_KEY`), `scrapers/rss_monitor.py` (devaluation alerts), `.github/workflows/url_health.yml`.

Dependencies (`requirements.txt`): `httpx, beautifulsoup4, lxml, PyYAML, networkx, rich, python-dotenv, feedparser, pytest, pdfplumber, pypdf`. Python 3.11/3.12. No browser, no Ollama.

`.env`: `PORTAL_CPP` (1.25), `SEATS_AERO_API_KEY` (optional), `MAX_CACHE_AGE_DAYS` (7), `SQLITE_PATH`.

Run: `./run.sh --origin JFK --dest NRT --cabin business --cash 4200 --offline` (offline uses `rates_cache.json` seed data); drop `--offline` and/or add `--refresh-all` to scrape live. Diagnostics: `--validate-urls`, `--check-freshness`, `--check-alerts`.

### 6.3 If recreating `mini-scraper` instead (graph + vision prototype)
Build order per `CURSOR_INSTRUCTIONS.md`: `optimizer/models.py` → `storage/database.py` → `scrapers/base_scraper.py` → `scrapers/vision_scraper.py` → `optimizer/graph.py` → `optimizer/optimizer.py` → `scrapers/capital_one_scraper.py` → `main.py` (+ remaining scrapers) → `run.sh`/`README.md`. Deps add `playwright`, `pydantic`, `aiosqlite`, `tenacity`, `ollama`. Keep SQLite as source of truth; every edge needs `source_name`+`source_url`; never recommend a stale path; penalize one-way edges. **Heed the lab: make LifeMiles use `aggregator_html`, not vision.**

### 6.4 If recreating `scraper` (generic crawler)
`crawl/browser.py` (Playwright session) + `crawl/scope.py` (domain/depth/page caps, blocklist, sitemap, normalize) → `extraction/dom.py` + `extraction/vision.py` + `extraction/merge.py` → `storage/sqlite_store.py` + `storage/csv_export.py` → `scraper.py` (BFS, 1-hour cap, per-page checkpoint, both-modes-fail accounting). Deps: `playwright, beautifulsoup4, lxml, pandas, requests, ollama, pyyaml, apscheduler`. Configure seeds/scope in `config.yaml`. Expect bot-blocking and slow vision — this is the pilot, not the destination.

### 6.5 Verification when recreating
- Reproduce the worked example: Turkish domestic economy ≈ **2.667¢/mile**.
- Confirm a 1:1 partner with a cheaper award than the portal yields CPP > portal, and that a path beating portal by <20% returns verdict `comparable`, ≥20% returns `best`/`tentative_best`.
- Confirm an all-fallback edge is flagged `hardcoded_fallback`, disagreeing sources flag `sources_disagree`, and stale top paths are never `recommended`.
- Run `pytest` in `AggregateScraper/` (hardening, June-18 regression, remediation suites).

