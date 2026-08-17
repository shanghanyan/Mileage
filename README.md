# Mileage

**A points-to-flights optimizer that tells you the truth about whether transferring your credit card points is actually worth it.**

Redeeming credit card points for flights is a comparison problem hidden behind paywalls, stale blog posts, and airline sites that actively block scrapers. Mileage answers one question honestly, with evidence: *given a route, a cabin, and a points balance, should you transfer to a partner airline or just book the portal floor?* It's built as a federated data pipeline — live scraping, third-party flight/fare APIs, and a verification layer that will not name a winner without provenance — on top of a multi-user-ready backend with Redis-backed caching, shared quota management, and OpenTelemetry tracing.

Every verdict ships with its receipts: source, timestamp, trust weight, and a confidence score. "Just use your portal, transferring isn't worth it" is a valid — and frequent — answer.

---

## The thesis, in two queries

The system exists because the best redemption is non-obvious in ways a flat
route table cannot express. Same bank, same currency, same alliance:

```
$ mileage quote --from HND --to ITM --cabin business --currency chase_ur \
      --miles 200000 --card sapphire_reserve
  *   12,750 pts  $     24   Chase UR -> Avios [oneworld] on Japan Airlines

$ mileage quote --from LHR --to JFK --cabin business --currency chase_ur \
      --miles 200000 --card sapphire_reserve
  *   34,000 pts  $    240   Chase UR -> Iberia Plus [oneworld] on American Airlines
      40,000 pts  $    240   Chase UR -> Avios [oneworld] on American Airlines
      40,000 pts  $    560   Chase UR -> Avios [oneworld] on British Airways
```

The last two rows are the point. Identical currency, identical chart, identical
40,000-Avios price — and $320 apart in cash, because one flies American metal
and the other flies British Airways. That difference comes from
`knowledge/fuel_charges.yaml`, which is keyed on **(currency × operating
carrier)** rather than on the program (§4.4). Key it on the program and the two
collapse into one number, and the engine confidently recommends the worse one.

The 12,750 comes from per-segment distance banding (§4.3): a 251-mile segment
is priced as a 251-mile segment, not as a flat "North America ↔ North Asia" fare.

**No cash-fare API is involved in any of those dollar figures.** Price paid —
government taxes, airport add-ons, carrier surcharges — comes from our own
tables, which is why comparing against live market fares stays out of scope
without the product losing its dollar column.

---

## Three things this refuses to do

- **Guess at availability.** `space_unknown` (nothing checked) and `no_space`
  (checked, none found) are different states and are never collapsed. A silent
  default that reads like a confirmed negative is worse than an error.
- **Fabricate cents-per-point.** With no market fare, `cpp` is `null`, not
  `0.0`. Points and price paid are always answerable, so a missing fare costs
  one column, not the answer.
- **Hide a route you can't book yet.** A route gated behind a card you don't
  hold is listed separately with the card named and its fee — "3 routes require
  a Sapphire card ›". What a card is worth on a trip you're actually trying to
  take is useful output, not a filter.

---

## Highlights

- **A working, real scraper — not a stub.** The aggregator (`providers/aggregator/`) pulls live award charts from public partner pages (Aeroplan, LifeMiles, Turkish, ANA, KrisFlyer, EVA, Flying Blue, Alaska, American, Qatar, Iberia, and more) through a resilient fetch stack: `httpx` for plain pages, `curl_cffi` TLS/JA4 impersonation for stricter hosts, and Wayback Machine / RSS / PDF fallbacks when a page is unreachable. An adaptive per-domain throttle backs off on `429`s and rotates sources instead of hammering. Chart targets live in `knowledge/sources.yaml` (~32 as of 2026-07-28) — run `mileage sources --validate-urls --deep` for a live read.
- **Provenance-first verification.** A number only enters the graph if it came from a selector that actually hit real content. Two sources that mirror the same published chart don't count as independent confirmation — cross-checking only counts across genuinely different sources. This is enforced by CI, not just documented: `mileage eval` feeds the verification layer a poisoned dataset (a garbage value, an unsourced datum, a stale chart) and asserts each one gets caught and demoted before it can become a `best`.
- **A second, LLM-assisted data intake — with hallucination guardrails baked in.** Beyond scraping known URLs, the aggregator can also ingest newsletters, creator blog posts, and video transcripts, running each through a local extractor that turns prose into structured chart rows. Every extracted number is checked against the source text verbatim — a `miles` value that doesn't literally appear in the document is dropped, no matter how confident the model is. Extracted data is flagged and demoted relative to directly-scraped data until an independent source confirms it.
- **Multi-user from the storage layer up, not bolted on.** Cache, rate-limiter, and lock are interfaces from day one, so the move from in-process dicts to a shared Redis backend was an adapter swap, not a rewrite. Two users hitting the same route concurrently trigger one live scrape, both served from cache, with a single atomic quota counter shared across every user — verified in `mileage demo-multiuser`.
- **Full-stack, not just a script.** FastAPI backend with bearer auth, a Vite/React frontend, SQLite persistence, OpenTelemetry tracing (Arize AX-compatible) so every run is replayable, and a golden-route regression suite that runs in CI and fails the build on any dishonest answer.
- **~190 automated tests, hermetic by construction** — the suite pins a deterministic fixture mode so it never depends on (or can be blocked by) a live network call, and the one live sweep is deselected by default rather than left to poison the runs after it. Full suite: ~19s. Beyond the golden routes there are property tests (ranking is deterministic and order-independent; first class never ranks below business; rounded transfer quantities always cover the award; every offered route's carrier actually serves the pair) and the §8 compile gate, which fails the build on a dangling cross-reference or a stamp older than 90 days.

---

## How it works

```
route + cabin + points  →  provider registry  →  verification (provenance, trust, freshness)  →  graph + verdict
                            (scraper, flight APIs,                                                (portal_only /
                             curated award charts)                                                comparable / best)
```

The domain logic (`domain/`) never imports from any data source — every provider, scraper, and extractor plugs into one interface and can be deleted without touching the core. That separation is what makes the honesty guarantees possible: the verification layer treats a live scrape and a $-API response identically, and can't be talked into trusting one over the other except by evidence (source trust weight, freshness, independence).

```
mileage/
  domain/      # pure logic — no I/O, no clock
    geo.py       # airport reference: region, position, great-circle, haul band
    service.py   # Table 3 (who flies it) + partner rights (who may book it)
    fuel.py      # §4.4 (currency x operating carrier) -> price paid
    rank.py      # §6.1 ranking; takes as_of, does no I/O
    cards.py     # card products and the gates they satisfy
    charts.py    # award-chart resolution (region, zone, and distance bands)
  providers/   # every data source behind one interface
    aggregator/  # the real scraper — fetch, parse, politeness/throttling, email + blog + transcript intake
  verify/      # provenance, trust, freshness, cross-checking, anti-hallucination bounds
  graph/       # TRANSFER* -> REDEEM enumeration + pricing (NetworkX)
  store/       # SQLite persistence + swappable Cache/RateLimiter/Lock (in-process or Redis)
  api/         # FastAPI backend + bearer auth
  knowledge_snapshot.py  # §8 compile step: integrity + staleness + content hash
  cli.py       # full pipeline, no web stack required
ui/            # Vite + React frontend
```

**The knowledge tables**, all version-controlled and human-reviewed. Scrapers
write `bonus_calendar.yaml` (Table 2, promotions) and nothing else:

| File | Table | What it decides |
|---|---|---|
| `airports.yaml` | ref | region + position for every airport, in one row so they can't drift apart |
| `carriers.yaml` | 3 | which carrier flies which pair (hubs + served regions, not 40k hand-entered rows) |
| `partners.yaml` | 1 | which carriers a currency may book |
| `fuel_charges.yaml` | 1 | carrier surcharges keyed on (currency × operating carrier) |
| `charts.yaml` | 1 | award charts — region matrix, zone matrix, and per-segment distance bands |
| `ratios.yaml` | 1 | bank → program transfer ratios |
| `alliances.yaml` | 1 | alliance membership + airline→airline transfer edges (Avios family) |
| `cards.yaml` | 1 | card products, portal rates, and transfer gates |
| `bonus_calendar.yaml` | **2** | live promotions — scraper-owned, never asserted on by tests |

`mileage compile` validates every cross-reference and freshness stamp and emits
a content hash. That hash namespaces every cache key, so editing a chart
invalidates the cache instead of being masked by it for the 2-day TTL.

---

## Setup

Requires Python 3.10+.

```bash
git clone <this-repo>
cd Mileage
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

That installs the core (CLI, API, scraper with `httpx`, SQLite). Everything runs with **zero API keys** — without external keys the fare chain falls back to a curated, clearly-flagged estimate instead of guessing.

**Optional extras**, install any combination:

```bash
pip install -e ".[aggregator]"     # curl_cffi TLS impersonation, PDF/RSS parsing, brotli/zstd decoding
pip install -e ".[discovery]"      # blog + transcript ingestion (readability extraction, YouTube captions)
pip install -e ".[multiuser]"      # Redis-backed cache/quota/locks for the multi-user backend
pip install -e ".[observability]"  # ship OpenTelemetry traces to Arize AX
pip install -e ".[dev]"            # pytest
```

Each extra degrades gracefully when absent — e.g. no `curl_cffi` just turns off TLS impersonation rather than crashing; no Redis URL falls back to in-process caching automatically.

### Run it

```bash
# Both flagship demos side by side: the "honest floor" case and the "hidden value" case
python -m mileage.cli demo

# One route, from the command line
python -m mileage.cli quote --from LAX --to IST --cabin business \
    --currency capital_one --miles 90000 --card venture_x

# Machine-readable
python -m mileage.cli quote --from LAX --to IST --cabin business --miles 90000 --json
```

Sample output — the "hidden value" case, where transferring beats the portal by 638%, backed by a live confirmed seat:

```
VERDICT: best — TRANSFER WINS
Capital One -> turkish returns 9.22c/pt vs the 1.25c/pt portal floor (638% better).
Live award space: turkish 45,000mi (2 seats)
```

And the "honest floor" case, where the tool correctly tells you not to bother:

```
VERDICT: comparable — COMPARABLE — transfer roughly ties the portal
Capital One -> lifemiles (1.30c/pt) is within 20% of the portal floor (1.25c/pt).
```

### Run the full stack (API + web UI)

```bash
# Terminal 1
uvicorn mileage.api.app:app --reload --port 8000

# Terminal 2
cd ui && npm install && npm run dev
```

Open `http://localhost:5173`.

### Run the test suite / CI honesty gate

```bash
pip install -e ".[dev]"
pytest                              # ~150 tests collected; hermetic offline

python -m mileage.cli eval          # golden-route regression + extraction-accuracy gate
python -m mileage.cli demo-observability   # watch the anti-hallucination guard reject a poisoned dataset live
```

### Other useful commands

```bash
python -m mileage.cli providers                    # provider health, quota used/remaining
python -m mileage.cli sources --validate-urls       # scraper target health check (URL rot detection)
python -m mileage.cli demo-degrade                  # disable/exhaust a provider mid-run, watch graceful fallback
python -m mileage.cli demo-multiuser                # two concurrent users, one shared scrape, per-user verdicts
python -m mileage.cli discover --dry-run            # preview rows extracted from the newsletter/blog intake
```

---

## What's built so far

- End-to-end verified quote pipeline: route → live/curated data → cross-checked verdict, with full provenance
- A real scraper with resilient fetching, adaptive politeness, and multi-format parsing (HTML tables, JSON, RSS, PDF)
- Provider federation with quota guards, caching, and ordered fallbacks — no single source can crash a run
- FastAPI backend + React frontend running the identical pipeline as the CLI
- Multi-user backend: shared cache, global quota counter, per-user balances, bearer auth, Redis-swappable storage
- OpenTelemetry tracing and a CI-enforced golden-route regression suite that fails the build on any hallucinated or unsourced answer
- A secondary data-discovery pipeline (newsletters, blog posts, video transcripts) with a verbatim-grounding guard so extracted numbers can never be invented
- Four additional transferable currencies (Amex MR, Chase UR, Citi ThankYou, Bilt) wired into the same ratio graph as Capital One — see the dormant-features note below on their source confidence
- An eval harness for the discovery extractor (`mileage/extraction_eval.py`) plus a swappable `OllamaExtractor` skeleton — see "Built but dormant" below

## Built but dormant

Some things are fully coded and wired but don't do anything useful yet, either for lack of an API key or because they're intentionally unfinished. Worth knowing before assuming a feature is live:

- **Amadeus** (the primary cash-fare source) — needs `AMADEUS_CLIENT_ID`/`AMADEUS_CLIENT_SECRET`. When unset it reports `DOWN` and fares fall back to Travelpayouts YAML cache → curated `fares.yaml`. When set, Flight Offers Search is live and uses the quote `start_date` when provided.
- **seats.aero** (optional paid live-award-space) — needs `SEATS_AERO_API_KEY`. When unset, live L3 is skipped in online mode (offline demos use the starnet fixture). When set, Partner API search is live and date-window aware.
- **URL rediscovery** (`mileage sources --rediscover`) — fully built (rot detection, search-then-validate-before-adopt), but dormant: no `SERPAPI_API_KEY`/`BING_SEARCH_API_KEY` and `MILEAGE_URL_REDISCOVERY` isn't set, so a rotted source is detected but never auto-replaced.
- **Duffel and AeroDataBox** — named in the plan as fallback providers, never scaffolded (no `providers/duffel.py` or equivalent exists).
- **The local LLM extractor** (Qwen2.5 via Ollama, §6.2/§6.3) — a real `OllamaExtractor` + GBNF grammar + eval harness now exist (`providers/aggregator/extract/local_extractor.py`, `extract/grammar.gbnf`, `mileage/extraction_eval.py`), but none of it has been exercised against a running model — every discovery intake still defaults to the keyless `DeterministicExtractor` unless `MILEAGE_EXTRACTOR_BACKEND=ollama` is set AND Ollama is actually running.
- **Chase/Amex/Citi/Bilt ratios** — real and sourced (issuer support pages / Award Travel Finder / The Points Guy, all fetched and dated in `knowledge/ratios.yaml`), but at lower trust than the Capital One block: the official issuer transfer-partner pages are JS-rendered and couldn't be fetched directly to cross-check against secondary aggregators. Treat these four as single-sourced until re-verified against the issuer's own page.
- **Arize tracing** — needs `ARIZE_SPACE_ID`/`ARIZE_API_KEY` plus `pip install -e ".[observability]"`. Verified live on the beta checklist when configured.
- **aviationstack** — L1 schedules only. `HEALTHY` when `AVIATIONSTACK_API_KEY` is set and probes `/v1/flights`; does not produce cash fares or award quotes (no ScheduleQuote type yet).
- **The Brain (Engine B)** — Phase 6, intentionally quarantined and unbuilt; the working product never depends on it.
- **Transfer bonuses** — live via `mileage refresh-bonuses` (Roame JSON-LD → `bonus_calendar.yaml`). Cap One→EVA +30% (and peers) apply in the graph while `valid_until` is in the future.

## What's next

- **Live award-space as the default.** Prefer seats.aero (or a future public L3) so "is there a seat" is live every time, not fixture-only offline.
- **Distance-band chart parsers** for Avios/Cathay (and similar) so those ATF pages resolve to route quotes.
- **Issuer-page re-verification** for Amex/Chase/Citi/Bilt transfer ratios.
- **Month-long watch demos** — `mileage refresh-bonuses` + `check-watches` + `scrape-daily` on fixed routes to show bonuses/space/fares moving over time.
- **True multi-card ranking UX** — "I hold points across three programs…" north star.

---

## Design principles

1. **No hallucinations.** A number enters the graph only from a source that produced a verifiable value.
2. **Source-agnostic core.** The domain logic consumes a normalized quote, never a scraper- or API-shaped row — any source can be swapped or removed without touching the core.
3. **Cross-checking requires independence.** Two sources echoing the same published chart is not confirmation.
4. **Graceful degradation.** A useful, honest answer even if a provider — or a whole data layer — returns nothing.
5. **Honest conclusions.** "Just use your portal" is a valid, frequent answer — the tool is willing to tell you not to bother.
6. **Provenance and freshness are first-class.** Every datum carries its source, timestamp, trust weight, and age-decayed confidence.
