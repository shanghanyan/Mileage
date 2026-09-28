"""Per-domain rate limit (≥ 60s)."""

from __future__ import annotations

import time
from collections import defaultdict
from urllib.parse import urlparse

DEFAULT_MIN_INTERVAL_SECONDS = 60.0

_last_hit: dict[str, float] = defaultdict(float)


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def wait_for_domain(url: str, min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS) -> float:
    """Sleep if the same domain was requested too recently. Returns seconds waited."""
    host = domain_of(url)
    now = time.monotonic()
    elapsed = now - _last_hit[host]
    waited = 0.0
    if _last_hit[host] and elapsed < min_interval:
        waited = min_interval - elapsed
        time.sleep(waited)
    _last_hit[host] = time.monotonic()
    return waited
