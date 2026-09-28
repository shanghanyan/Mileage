from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
import re

ConfidenceLevel = Literal["high", "medium", "low", "unverified"]


@dataclass
class ScrapedRow:
    """One data point from one source. Immutable after construction."""

    source_name: str
    source_url: str
    scraped_at: datetime

    from_program: str
    to_program: str

    transfer_ratio: float | None = None

    origin_zone: str | None = None
    destination_zone: str | None = None
    economy_miles: int | None = None
    business_miles: int | None = None
    first_miles: int | None = None

    raw_cell_text: str = ""
    selector_matched: bool = False
    confidence: ConfidenceLevel = "unverified"
    flags: list[str] = field(default_factory=list)
    source_updated_at: datetime | None = None
    source_trust: float = 0.40
    source_count: int = 1
    miles_range_low: int | None = None
    miles_range_high: int | None = None

    def is_usable(self) -> bool:
        if not self.selector_matched:
            return False
        has_data = any(
            v is not None
            for v in (
                self.transfer_ratio,
                self.economy_miles,
                self.business_miles,
                self.first_miles,
            )
        )
        return has_data and "hallucinated" not in self.flags

    def to_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "source_url": self.source_url,
            "scraped_at": self.scraped_at.isoformat(),
            "from_program": self.from_program,
            "to_program": self.to_program,
            "transfer_ratio": self.transfer_ratio,
            "origin_zone": self.origin_zone,
            "destination_zone": self.destination_zone,
            "economy_miles": self.economy_miles,
            "business_miles": self.business_miles,
            "first_miles": self.first_miles,
            "raw_cell_text": self.raw_cell_text,
            "selector_matched": self.selector_matched,
            "confidence": self.confidence,
            "flags": list(self.flags),
            "source_updated_at": (
                self.source_updated_at.isoformat() if self.source_updated_at else None
            ),
            "source_trust": self.source_trust,
            "source_count": self.source_count,
            "miles_range_low": self.miles_range_low,
            "miles_range_high": self.miles_range_high,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScrapedRow":
        scraped_at = data.get("scraped_at")
        if isinstance(scraped_at, str):
            scraped_at = datetime.fromisoformat(scraped_at.replace("Z", "+00:00"))
        elif scraped_at is None:
            scraped_at = datetime.now(timezone.utc)
        source_updated_at = data.get("source_updated_at")
        if isinstance(source_updated_at, str):
            source_updated_at = datetime.fromisoformat(
                source_updated_at.replace("Z", "+00:00")
            )
        return cls(
            source_name=data["source_name"],
            source_url=data["source_url"],
            scraped_at=scraped_at,
            from_program=data["from_program"],
            to_program=data["to_program"],
            transfer_ratio=data.get("transfer_ratio"),
            origin_zone=data.get("origin_zone"),
            destination_zone=data.get("destination_zone"),
            economy_miles=data.get("economy_miles"),
            business_miles=data.get("business_miles"),
            first_miles=data.get("first_miles"),
            raw_cell_text=data.get("raw_cell_text", ""),
            selector_matched=data.get("selector_matched", False),
            confidence=data.get("confidence", "unverified"),
            flags=list(data.get("flags", [])),
            source_updated_at=source_updated_at,
            source_trust=data.get("source_trust", 0.40),
            source_count=data.get("source_count", 1),
            miles_range_low=data.get("miles_range_low"),
            miles_range_high=data.get("miles_range_high"),
        )


def parse_miles_text(raw: str) -> int | None:
    """Parse '90K', '90,000', '90000', '90.0K' into an integer."""
    raw = raw.strip().replace(",", "").replace(" ", "")

    if m := re.fullmatch(r"(\d+(?:\.\d+)?)[kK]", raw):
        return int(float(m.group(1)) * 1000)

    if m := re.fullmatch(r"(\d{4,7})", raw):
        return int(m.group(1))

    if m := re.fullmatch(r"(\d+)", raw):
        val = int(m.group(1))
        return val if val >= 1000 else None

    return None
