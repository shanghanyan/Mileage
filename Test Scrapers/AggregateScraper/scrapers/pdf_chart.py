"""PDF award chart extraction (e.g. official KrisFlyer PDFs)."""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone

from scrapers.base import ScrapedRow, parse_miles_text

log = logging.getLogger(__name__)


def parse_pdf_chart(
    pdf_bytes: bytes,
    program: str,
    url: str,
    source_name: str,
) -> list[ScrapedRow]:
    """
    Extract zone/mile rows from a PDF award chart.
    Uses pdfplumber for table extraction; falls back to text regex.
    """
    try:
        import pdfplumber
    except ImportError:
        log.warning("pdfplumber not installed — skipping PDF parse for %s", url)
        return _parse_pdf_text_fallback(pdf_bytes, program, url, source_name)

    rows: list[ScrapedRow] = []
    now = datetime.now(timezone.utc)

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables() or []
            for table in tables:
                if len(table) < 2:
                    continue
                header = [str(c or "").lower() for c in table[0]]
                has_zones = any("zone" in h or "from" in h or "to" in h for h in header)
                has_miles = any(
                    "business" in h or "economy" in h or "mile" in h or "point" in h
                    for h in header
                )
                if not has_miles:
                    continue

                for tr in table[1:]:
                    if len(tr) < 2:
                        continue
                    cells = [str(c or "").strip() for c in tr]
                    origin = cells[0] if cells else ""
                    dest = cells[1] if len(cells) > 1 else ""
                    if not origin:
                        continue

                    economy = business = first = None
                    for idx, cell in enumerate(cells[2:], start=2):
                        miles = parse_miles_text(cell.replace(",", ""))
                        if miles is None:
                            continue
                        h = header[idx] if idx < len(header) else ""
                        if "business" in h or "j" in h.split():
                            business = miles
                        elif "first" in h or "f" in h.split():
                            first = miles
                        elif "economy" in h or business is None:
                            economy = miles

                    if economy is None and business is None and first is None:
                        continue

                    rows.append(
                        ScrapedRow(
                            source_name=source_name,
                            source_url=url,
                            scraped_at=now,
                            from_program=program,
                            to_program="award",
                            origin_zone=origin or "Zone",
                            destination_zone=dest or origin,
                            economy_miles=economy,
                            business_miles=business,
                            first_miles=first,
                            raw_cell_text=" | ".join(cells[:5]),
                            selector_matched=True,
                            flags=["pdf_source"],
                        )
                    )

    if not rows:
        rows = _parse_pdf_text_fallback(pdf_bytes, program, url, source_name)

    return rows


def _parse_pdf_text_fallback(
    pdf_bytes: bytes,
    program: str,
    url: str,
    source_name: str,
) -> list[ScrapedRow]:
    """Regex extraction from raw PDF text when table parsing fails."""
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("pypdf not installed — cannot parse PDF at %s", url)
        return []

    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    now = datetime.now(timezone.utc)
    rows: list[ScrapedRow] = []

    pattern = re.compile(
        r"(?:zone|region|from)\s*[\d\w\s-]{0,30}?"
        r"(\d{2,3}[,.]?\d{3})\s*(?:miles|points|krisflyer)?",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        raw = m.group(1).replace(",", "").replace(".", "")
        try:
            miles = int(raw)
        except ValueError:
            continue
        if miles < 5000:
            continue
        rows.append(
            ScrapedRow(
                source_name=source_name,
                source_url=url,
                scraped_at=now,
                from_program=program,
                to_program="award",
                origin_zone="PDF chart",
                destination_zone="PDF chart",
                business_miles=miles,
                raw_cell_text=m.group(0)[:100],
                selector_matched=True,
                flags=["pdf_source", "pdf_text_fallback"],
            )
        )
        if len(rows) >= 50:
            break

    return rows
