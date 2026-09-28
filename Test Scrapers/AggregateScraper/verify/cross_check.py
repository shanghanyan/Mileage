from scrapers.base import ScrapedRow
from verify.trust import attach_trust, consensus_rate, compute_trust

AUTHORITATIVE_SOURCES = {"capitalone.com", "partners.yaml"}
TOLERANCE = 0.10
FALLBACK_TRUST = 0.30


def cross_check(
    rows: list[ScrapedRow],
    field: str = "economy_miles",
) -> tuple[ScrapedRow | None, list[str]]:
    flags: list[str] = []

    for r in rows:
        if r.source_trust == 0.40 and r.source_updated_at:
            attach_trust(r)
        elif r.source_name == "fallback_rates.json":
            r.source_trust = r.source_trust or FALLBACK_TRUST

    authoritative = [
        r for r in rows if r.source_name in AUTHORITATIVE_SOURCES and r.is_usable()
    ]
    if authoritative:
        best = authoritative[0]
        best.source_count = len(rows)
        return best, flags

    usable = [r for r in rows if r.is_usable()]
    if len(usable) == 0:
        return None, ["no_usable_rows"]

    fallback_only = all(r.source_name == "fallback_rates.json" for r in usable)
    if fallback_only and len(usable) == 1:
        row = usable[0]
        row.flags.append("hardcoded_fallback")
        trust = row.source_trust or FALLBACK_TRUST
        row.confidence = "medium" if trust >= 0.55 else "low"
        row.source_trust = trust
        return row, flags + ["⚠ hardcoded fallback"]

    if len(usable) == 1:
        row = usable[0]
        row.source_count = 1
        row.confidence = "medium" if row.source_trust >= 0.65 else "low"
        return row, flags + ["single_source"]

    values = [getattr(r, field) for r in usable if getattr(r, field) is not None]
    if not values:
        return None, ["no_numeric_values"]

    try:
        consensus_val, winner = consensus_rate(usable, field=field)
    except ValueError:
        return None, ["consensus_failed"]

    avg = sum(values) / len(values)
    spread = (max(values) - min(values)) / avg if avg > 0 else 0

    winner.source_count = len(usable)
    setattr(winner, field, consensus_val)

    if spread > TOLERANCE:
        flags.append(f"sources_disagree_{spread:.0%}")
        winner.confidence = "low"
    elif winner.source_trust >= 0.85 and len(usable) >= 2:
        winner.confidence = "high"
    elif len(usable) >= 2:
        winner.confidence = "medium"
    else:
        winner.confidence = "low"

    return winner, flags


def group_transfer_rows(rows: list[ScrapedRow]) -> dict[tuple, list[ScrapedRow]]:
    groups: dict[tuple, list[ScrapedRow]] = {}
    for row in rows:
        key = (row.from_program, row.to_program)
        groups.setdefault(key, []).append(row)
    return groups


def group_award_rows(rows: list[ScrapedRow]) -> dict[tuple, list[ScrapedRow]]:
    groups: dict[tuple, list[ScrapedRow]] = {}
    for row in rows:
        if not row.origin_zone or not row.destination_zone:
            continue
        key = (row.from_program, row.origin_zone, row.destination_zone)
        groups.setdefault(key, []).append(row)
    return groups
