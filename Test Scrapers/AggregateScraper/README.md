# AggregateScraper

Capital One Miles Optimizer — ranks every verified path from C1 miles to a United (Star Alliance) flight seat by **cents per mile (CPM/CPP)**.

## Principles

- **No hallucinations** — data enters the graph only from scraped sources with selector hits
- **Cross-check** — two independent sources must agree (Capital One is authoritative for transfer ratios)
- **Live precedence** — seats.aero live mileage overrides static zone charts when available
- **Honest conclusions** — never declares a winner without verified data on both portal and transfer paths

## Two Paths

| Path | Flow | Floor |
|------|------|-------|
| **A — Portal** | C1 → Capital One Travel → United cash fare | 1.0¢ (Venture) or 1.25¢ (Venture X) |
| **B — Transfer** | C1 → Star Alliance partner → United award | Must beat portal by ≥20% to recommend |

## Quick Start

```bash
cd AggregateScraper
chmod +x run.sh

# Offline demo (uses config/rates_cache.json seed data)
./run.sh --origin JFK --dest NRT --cabin business --cash 4200 --offline

# With fees
./run.sh --origin JFK --dest NRT --cabin business --cash 4200 --fees 75 --offline

# Force live scrape (requires network)
./run.sh --origin JFK --dest NRT --cabin business --cash 4200 --refresh-all

# Exclude connecting itineraries (KrisFlyer via SIN, EVA via TPE)
./run.sh --origin JFK --dest NRT --cabin business --cash 4200 --nonstop-only --offline

# Validate scrape target URLs (HEAD check)
./run.sh --validate-urls

# Freshness report
./run.sh --check-freshness

# Devaluation RSS check
./run.sh --check-alerts
```

## Configuration

| File | Purpose |
|------|---------|
| `config/partners.yaml` | All 22 C1 transfer partners, ratios, route metadata |
| `config/scrape_targets.yaml` | Blog/aggregator URL chains per program |
| `config/rss_feeds.yaml` | RSS/Atom feeds polled in parallel (bot-block workaround) |
| `config/sources.yaml` | Legacy URLs, RSS devaluation feeds, block signatures |
| `config/zone_mapping.yaml` | Airport IATA → program zone names |
| `config/rates_cache.json` | Last verified scraped rows (timestamped) |
| `data/fallback_rates.json` | Last-known-good rates when scrapers fail |
| `.env` | `PORTAL_CPP`, `SEATS_AERO_API_KEY`, `MAX_CACHE_AGE_DAYS`, `SQLITE_PATH` |

## Architecture

```
scrapers/     httpx + BS4 fetchers → ScrapedRow
verify/       cross-check, freshness decay, CPP bounds
graph/        NetworkX DiGraph builder + path ranker
pipeline/     orchestrator ties scrape → verify → optimize
display/      Rich terminal leaderboard
db/           SQLite persistence (edges, runs, alerts)
```

## Optimizations vs. Original Plan

1. **httpx-first** — no Playwright/Ollama dependency for core chart scraping (faster, lighter, more reliable)
2. **Unified `ScrapedRow` contract** — single pipeline from any source
3. **Cache-aware orchestrator** — only re-scrapes stale datasets per `sources.yaml` schedule
4. **Conclusion engine** — enforces 20% margin rule and "tentatively best" for flagged winners
5. **seats.aero integration** — live mileage takes precedence over zone charts per route query
6. **Wayback Machine fallback** — bot-blocked pages fetched via archive.org snapshots
7. **RSS award feeds** — parallel polling from 10xTravel/OMAAT/Frequent Miler feeds
8. **Monthly URL health CI** — `.github/workflows/url_health.yml` HEAD-checks all targets

## seats.aero

Set `SEATS_AERO_API_KEY` in `.env` for live award availability on the queried route. Without it, the optimizer uses scraped zone charts and `data/fallback_rates.json` when scrapers fail or cache exceeds `MAX_CACHE_AGE_DAYS` (default 7).

## Data Integrity Rules

1. No selector hit → no row (raises `SelectorMissError`, tries fallback URL)
2. Ambiguous cell text → skipped (never guessed)
3. Single source → not promoted to graph
4. Stale data → shown with warning, not silently dropped
