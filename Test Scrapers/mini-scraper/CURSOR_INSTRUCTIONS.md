# Capital One → United Airlines Point Optimizer — Build Spec

This file is the authoritative build specification for `mini-scraper`. **Do not change the CPP compounding formula or graph edge schema without updating every scraper's `to_edges()` and Section 8 together.**

## Critical domain fact

Capital One has **no** direct transfer to United MileagePlus. Paths to United flights go through Star Alliance partners: LifeMiles, Turkish M&S, KrisFlyer, or Aeroplan.

## CPP formula (Section 8)

```
path_cpp = product of all edge_cpp values along the path
```

| Edge type | edge_cpp |
|-----------|----------|
| Transfer (C1→partner) | transfer ratio (output per 1 input) |
| Redemption (partner→USD) | cash_price_cents / miles_needed |
| Direct cash (C1→USD) | cpp directly (0.5, 0.8, 1.0) |

**Worked example — Turkish domestic:** C1→Turkish ratio 1.0; $200 flight / 7,500 mi → `1.0 × (20000/7500) = 2.667¢` per C1 mile.

## Key constraints

1. No C1 → United MileagePlus edge.
2. Use `nx.MultiDiGraph` (multiple C1→USD redemptions).
3. CPP compounds by **product**, not sum.
4. `C1_CASHBACK` units are USD; face-value path uses `edge_cpp=100`.
5. Propagate `is_one_way` to path level.
6. Do not mark `recommended=True` on paths with `has_stale=True`.
7. SQLite is source of truth; optimizer reads edges from DB after upsert.
8. Every edge needs `source_name` and `source_url`.

## Project layout

See repository tree: `config/`, `scrapers/`, `optimizer/`, `storage/`, `main.py`, `run.sh`.

## Build order

1. `optimizer/models.py`
2. `storage/database.py`
3. `scrapers/base_scraper.py`
4. `scrapers/vision_scraper.py`
5. `optimizer/graph.py`
6. `optimizer/optimizer.py`
7. `scrapers/capital_one_scraper.py`
8. `main.py` → add remaining scrapers
9. `run.sh` + `README.md`

## Environment

See `.env.example`. Vision scrapers require Ollama (`OLLAMA_MODEL` fallback: `llama3.2-vision:11b` → `llava:13b` → `moondream2`).

## Full detail

The complete instruction set (scrapers 11a–11i, SQL schema, log formats, validation baselines, and `main.py` orchestration) was used to generate this project. Refer to `README.md` for usage and `config/scraper_config.yaml` for URLs and crops.
