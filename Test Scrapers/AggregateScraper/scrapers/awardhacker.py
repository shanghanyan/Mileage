"""AwardHacker route sanity check — not used as graph edges."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from scrapers.http_util import fetch_html

log = logging.getLogger(__name__)


async def check_route_ranking(
    origin: str,
    dest: str,
    config: dict,
) -> dict[str, int]:
    """Return {program: rank} from AwardHacker for sanity checking."""
    url = config.get("live_awards", {}).get("awardhacker", {}).get("url")
    if not url:
        return {}

    try:
        html = await fetch_html(url, block_signatures=config.get("block_signatures", []))
    except Exception as exc:
        log.warning("AwardHacker fetch failed: %s", exc)
        return {}

    soup = BeautifulSoup(html, "lxml")
    rankings: dict[str, int] = {}
    route_key = f"{origin.upper()}-{dest.upper()}"

    for block in soup.find_all(["div", "section", "tr"]):
        text = block.get_text(" ", strip=True)
        if route_key not in text and origin.upper() not in text:
            continue
        for i, program in enumerate(
            ["lifemiles", "aeroplan", "turkish", "ana", "krisflyer"], start=1
        ):
            if re.search(program, text, re.I):
                rankings[program] = i
    return rankings
