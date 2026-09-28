"""Trust scoring and multi-source consensus."""

from __future__ import annotations

from datetime import datetime, timezone

from scrapers.base import ScrapedRow


def compute_trust(source_updated_at: datetime | None) -> float:
    if source_updated_at is None:
        return 0.40

    age_days = (datetime.now(timezone.utc) - source_updated_at).days

    if age_days <= 30:
        return 1.00
    if age_days <= 60:
        return 0.85
    if age_days <= 120:
        return 0.65
    if age_days <= 180:
        return 0.45
    if age_days <= 365:
        return 0.25
    return 0.10


def consensus_rate(records: list[ScrapedRow], field: str = "business_miles") -> tuple[int, ScrapedRow]:
    """
    Weighted median of mile values, weighted by source_trust.
    Returns (consensus_miles, winning_record).
    """
    if not records:
        raise ValueError("No records to merge")
    if len(records) == 1:
        val = getattr(records[0], field) or 0
        return val, records[0]

    usable = [r for r in records if getattr(r, field) is not None]
    if not usable:
        raise ValueError("No numeric values in records")

    sorted_records = sorted(usable, key=lambda r: getattr(r, field) or 0)
    weights = [r.source_trust for r in sorted_records]
    total = sum(weights)
    cumulative = 0.0
    winner = sorted_records[-1]
    for record, w in zip(sorted_records, weights):
        cumulative += w
        if cumulative >= total / 2:
            winner = record
            break

    return getattr(winner, field) or 0, winner


def attach_trust(row: ScrapedRow) -> ScrapedRow:
    row.source_trust = compute_trust(row.source_updated_at)
    return row
