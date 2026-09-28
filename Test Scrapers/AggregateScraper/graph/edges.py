from dataclasses import dataclass
from datetime import datetime


@dataclass
class TransferEdge:
    from_currency: str
    to_currency: str
    ratio: float
    confidence: str
    source: str
    scraped_at: datetime
    flags: list[str]


@dataclass
class AwardEdge:
    from_currency: str
    origin_zone: str
    destination_zone: str
    economy_miles: int | None
    business_miles: int | None
    first_miles: int | None
    source: str
    confidence: str
    scraped_at: datetime
    flags: list[str]
    source_count: int = 1
    source_trust: float = 0.40
    miles_range_low: int | None = None
    miles_range_high: int | None = None


@dataclass
class PortalEdge:
    cpp: float = 1.0
    source: str = "capitalone.com"
