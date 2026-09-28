from datetime import datetime, timedelta, timezone

from scrapers.base import ScrapedRow

MEDIUM_THRESHOLD = timedelta(days=30)
STALE_THRESHOLD = timedelta(days=90)


def apply_freshness(row: ScrapedRow) -> ScrapedRow:
    age = datetime.now(timezone.utc) - row.scraped_at
    if age > STALE_THRESHOLD:
        row.flags.append("stale")
        row.confidence = "low"
    elif age > MEDIUM_THRESHOLD and row.confidence == "high":
        row.confidence = "medium"
    return row


def age_days(row: ScrapedRow) -> int:
    age = datetime.now(timezone.utc) - row.scraped_at
    return age.days
