# Capital One → United Airlines Point Optimizer

Scrapes live transfer ratios, Star Alliance award charts, and third-party valuations, then runs a weighted-graph optimizer to rank redemption paths by **cents per Capital One mile (CPP)**.

**Domain fact:** Capital One does not transfer directly to United MileagePlus. United flights are reached via LifeMiles, Turkish Miles & Smiles, KrisFlyer, or Aeroplan — each hop compounds transfer ratio and redemption value into one CPP.

## Quick start

```bash
cd mini-scraper
./run.sh
```

Requirements:

- Python 3.11 or **3.12** (recommended; 3.13 may fail on pinned `playwright`/`pydantic` wheels)
- Chromium (installed by `playwright install chromium` via `run.sh`)
- **Optional:** [Ollama](https://ollama.com) with a vision model for LifeMiles/Turkish chart scraping (`llama3.2-vision:11b`, `llava:13b`, or `moondream2`)

Without Ollama, vision scrapers fall back to `config/rates_cache.json`.

## Configuration

Copy `.env.example` to `.env` and adjust:

| Variable | Purpose |
|----------|---------|
| `OLLAMA_HOST` / `OLLAMA_MODEL` | Vision extraction |
| `SQLITE_PATH` | Edge persistence |
| `RATES_CACHE_PATH` | Human-readable scrape snapshot |
| `SCRAPE_MAX_MINUTES` | Max scrape wall time |
| `HEADLESS` | Playwright headless mode |

URLs, timeouts, and screenshot crops live in `config/scraper_config.yaml`.

## CPP formula

For any path to USD:

```
path_cpp = edge_cpp₁ × edge_cpp₂ × …
```

- **Transfer edges** (e.g. C1 → Turkish): `edge_cpp = transfer ratio`
- **Redemption edges** (e.g. Turkish → USD): `edge_cpp = cash_price_cents / miles_needed`
- **Direct cash** (e.g. statement credit): `edge_cpp = cpp` (0.5, 0.8, 1.0)

Example: Turkish domestic economy — 1:1 transfer, $200 / 7,500 mi → `1.0 × (20000/7500) ≈ 2.67¢` per C1 mile.

## Project layout

```
mini-scraper/
├── config/          scraper_config.yaml, rates_cache.json
├── scrapers/        Base scrapers + vision utility
├── optimizer/       Pydantic models, graph, PointOptimizer
├── storage/         SQLite (aiosqlite)
├── logs/            Per-scraper logs + optimization JSON
├── main.py          Async orchestrator
└── run.sh           Setup + run
```

## Data flow

1. Scrapers run in order (Capital One and AwardWallet first).
2. Edges upserted to SQLite; optimizer reads from DB.
3. `PointOptimizer` enumerates simple paths on a `MultiDiGraph` (parallel C1→USD redemptions preserved).
4. Rich tables show holdings, best paths, and warnings (stale, one-way, suspicious).

## Development portfolio

`main.py` uses a demo `FAKE_PORTFOLIO` (85k C1 miles, $200 cashback, 15k Turkish miles). Edit `UserPortfolio` there for your balances.

## Sanity checks

- TPG / NerdWallet valuations are **not** graph edges — used to warn when computed CPP exceeds TPG by >40%.
- AwardFares LifeMiles averages are compared to chart extractions.
- Section 16 baselines in `CURSOR_INSTRUCTIONS.md` trigger warnings if CPP deviates >30%.

## Full specification

See `CURSOR_INSTRUCTIONS.md` for load-bearing schemas, scraper contracts, and build order.
