# The Known World

The Known World is the set of version-controlled tables that describe how the
travel industry is arranged. It is what the engine believes to be true *before*
any scrape runs.

Its defining property: **live scrapes never write it.** A scrape can tell you a
price moved. It cannot tell you Aer Lingus joined an alliance. Those are facts a
human enters, cites, dates, and re-verifies — and everything downstream inherits
them, including facts nobody checked.

Everything here lives in `mileage/knowledge/` and is hashed into the snapshot id
that every result carries (e.g. `snap 6bfdbb007bad`). Change one byte and the
hash changes, every cache entry namespaced by it is invalidated, and the compile
step re-runs its integrity checks.

---

## The tables

Defined by `TABLE_FILES` in `mileage/knowledge_snapshot.py`. All paths are
relative to `mileage/knowledge/`.

| File | Asserts | Wrong data causes |
|---|---|---|
| `alliances.yaml` | Which programs belong to Star Alliance / SkyTeam / oneworld, plus program→program transfer edges and their ratios | Invented itineraries. Alliance membership grants booking rights on every carrier in that alliance |
| `partners.yaml` | Which carriers a currency may actually *book* (§4.5). Defaults to the alliance; `extra` and `excluded` record departures | A redemption that cannot be booked, ranked and presented as bookable |
| `carriers.yaml` | Which carrier *flies* a city pair (§ Table 3) — hubs, regions served, explicit routes | Routes that don't exist, or real routes invisible to search |
| `charts.yaml` | Award charts: region bands, zone matrices, miles per cabin | Wrong headline point cost |
| `ratios.yaml` | Bank/hotel currency → airline program transfer ratios | Wrong point cost, or a partner that silently can't be reached |
| `airports.yaml` | IATA codes, coordinates, region assignment, per-airport tax add-ons | Wrong distance band, wrong region band, wrong taxes |
| `fuel_charges.yaml` | Carrier surcharges keyed on (currency × operating carrier) | Wrong out-of-pocket price; the JAL-vs-BA Avios distinction collapses |
| `cards.yaml` | Card products, portal rates, which cards unlock which transfers | Wrong portal floor; wrong gating |
| `fares.yaml` | Curated fallback cash fares | Wrong cents-per-point |

Deliberately **not** in the Known World: `bonus_calendar.yaml` (scraper-owned,
changes weekly), `sources.yaml` (where to scrape, not what is true),
`discovered_charts.json` (scrape output), `travelpayouts_cache.yaml`.

---

## How to vet it

Start with `alliances.yaml`. It is the highest-leverage file in the repo because
membership is transitive into booking rights: one wrong line invents itineraries
across every route that touches that carrier.

Each roster now carries `source`, `url` and `verified_at`. The compile step
fails the build if any of those are missing — an unciteable claim is not
vettable, and an unvettable claim is how a program sits in the wrong alliance
for months.

To check the rosters against reality:

```bash
python -c "import yaml,sys; d=yaml.safe_load(open('mileage/knowledge/alliances.yaml'))
for k,v in d['alliances'].items():
    print(f\"\n{v['name']}  (verified {v.get('verified_at')})  {v.get('url','')}\")
    for p in v['programs']: print('   ', p)"
```

Then compare against the official member list each roster cites. Membership
changes are regular, not rare — SAS moved Star Alliance → SkyTeam in 2024, Oman
Air joined oneworld in 2025 — so `verified_at` going stale is a real signal, and
the compile step already treats records older than 90 days as a build failure.

### Full audit, 2026-08-18

Every table was checked against official sources. Nine factual errors found and
fixed; all of them produced wrong answers, none of them broke anything.

**Wrong alliance membership**

| Fact | Was | Now |
|---|---|---|
| Aer Lingus AerClub | `oneworld` | `independent` + bilateral/AJB rights |
| SAS EuroBonus | `star_alliance` | `skyteam` (moved 2024-09-01) |
| Aeroflot | active SkyTeam member | removed (suspended 2022) |

SAS is the same bug as AerClub in mirror image: SAS metal was bookable with
Aeroplan/United/Turkish miles and *unbookable* with Flying Blue/Delta —
precisely inverted.

**Partnerships that do not exist**

| Claim | Reality |
|---|---|
| Alaska can book Lufthansa | Never a Mileage Plan redemption partner. Alaska's only Lufthansa Group tie is ITA Airways, added Jan 2026 as **earn-only** |
| Chase → Etihad Guest | Etihad is an Amex/Capital One partner. Chase's airline list is exactly ten and Etihad is not on it |
| Amex → Finnair Plus | Finnair is a Capital One partner, not an Amex one |
| Citi → British Airways | No direct edge. The real path is Citi → Qatar → Avios |
| Virgin Atlantic ↔ Singapore | Partnership ended 2025-04-23. Was listed on **both** sides |

**Partnerships that exist but were invisible**

| Missing | Why it mattered |
|---|---|
| Capital One → JAL (4:3) | JAL has a chart here and the HND–ITM thesis route is *about* the JAL Avios band, yet Cap One could not reach JAL Mileage Bank at all |
| Citi → American (1:1) | Citi is the **only** bank that transfers to AAdvantage — no other currency could reach the AA chart directly |
| Bilt → AA / Iberia / JAL / Qatar / AerClub / TAP | Six real 1:1 edges to charts already in the repo |
| Chase → Aer Lingus / JetBlue, Amex → Iberia / AerClub / Delta / JetBlue | Real published partners |

**Modelling defects**

- `swiss` was a duplicate of `lufthansa`. Miles & More is one ledger shared by
  LH, LX and OS; it was modelled as two programs with two sets of booking
  rights and two surcharge rows, free to drift apart on any edit.
- Carrier id `JAL` was not an IATA code while all 45 others were. Live award
  feeds emit `JL`, so the id would silently fail to match the one carrier the
  flagship demo depends on. Renamed, and the compile step now rejects any
  non-IATA carrier id.

**Deliberately left alone.** Region assignments for Istanbul (Europe), Baku and
Tbilisi (Middle East), Panama City and Central America (North America) are all
defensible award-chart conventions rather than errors. All 227 airport
coordinates were spot-checked against reference values and are correct.

**Known incompleteness, not error.** Alliance rosters now match the official
member lists, but many members have no `carriers.yaml` record and are therefore
inert. Asiana leaves Star Alliance on 2026-12-17 when it merges into Korean
Air — revisit before that date.

### The failure this is designed to prevent

Aer Lingus AerClub was listed as a `oneworld` member in four files. It is not
one — Aer Lingus left oneworld in 2007. AerClub's redemption rights are
bilateral and joint-business only: Aer Lingus, British Airways and American for
reward flights, plus Iberia and Finnair through the IAG/AA transatlantic joint
business. It has never had alliance-wide access.

Because `partners.yaml` grants a currency booking rights on every carrier in its
alliance, that one word gave AerClub rights on all ~15 oneworld carriers. The
engine ranked `Avios → Aer Lingus AerClub [oneworld] on American Airlines` as
the **#1 option on four separate routes** in a twelve-day sweep — an itinerary
that cannot be booked, presented first, with no flag on it, because every file
agreed with every other file.

Two lessons, both now enforced:

1. **Agreement is not correctness.** All four files said `oneworld`, so no
   cross-check could have caught it. Only an external source can. Hence the
   mandatory `source` / `verified_at` on every roster.
2. **Convenience fictions get read literally.** `carriers.yaml` carried the
   comment `alliance: oneworld  # value-partner for Avios purposes` — someone
   knew it wasn't really true and wrote it down anyway. The partner-rights
   resolver does not read comments.

---

## What the compile step checks

`mileage/knowledge_snapshot.py`, run on every build and by
`tests/test_properties.py::test_knowledge_tables_pass_referential_integrity`.

Mechanical checks, all build failures:

- Every `program_transfers[from|to]` resolves to a known program
- Every transfer-ratio destination resolves to a chart, alliance or carrier
- Every carrier named in `fuel_charges.yaml` / `partners.yaml` exists in `carriers.yaml`
- Every hub and explicit route airport exists in `airports.yaml`
- Every chart program naming an alliance names a real one
- **Alliance membership agrees across all four files that assert it**
- **Every alliance roster carries `source` and `verified_at`**
- **Every carrier id is a 2-character uppercase IATA code**
- No record older than 90 days (`STALENESS_DAYS`)

The last two are new, added with the AerClub fix. The consistency check cannot
catch a fact that is uniformly wrong — that is what human vetting is for — but
it does guarantee that a correction applied to one file cannot leave the other
three quietly wrong, which is the realistic failure mode when fixing this class
of bug.

---

## What is NOT in the Known World, and should be

Recorded here rather than left as a silent absence:

- **Seasonal / peak–off-peak award pricing.** `Route` has no date field and
  `domain/charts.py` reads no date. Real charts (BA, Aeroplan, Flying Blue)
  price by date; this engine cannot. `tests/test_date_stability.py` is the
  tripwire that fires when this changes.
- **Per-card transfer ratio variation.** `ratios.yaml` notes that Citi's 1:1
  rates are top-tier-card only, and models a single ratio anyway. Treat those as
  a ceiling.
- **Live award inventory.** Not a table and never will be — but worth stating
  that with `SEATS_AERO_API_KEY` unset, the only L3 data is a two-row offline
  fixture, so `no_space` in a sweep means "not covered by the fixture", not
  "no seats exist".
