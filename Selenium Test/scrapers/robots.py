"""Check robots.txt and document the decision (does not silently ignore)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

DEFAULT_UA = "PointyResearchBot/0.1 (+local one-time research scrape)"


@dataclass
class RobotsDecision:
    allowed: bool
    robots_url: str
    user_agent: str
    note: str

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "robots_url": self.robots_url,
            "user_agent": self.user_agent,
            "note": self.note,
        }


def check_robots(url: str, user_agent: str = DEFAULT_UA, timeout: float = 10.0) -> RobotsDecision:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        response = httpx.get(robots_url, timeout=timeout, follow_redirects=True)
        if response.status_code >= 400:
            return RobotsDecision(
                allowed=True,
                robots_url=robots_url,
                user_agent=user_agent,
                note=f"robots.txt HTTP {response.status_code}; treating as allowed and documenting",
            )
        parser.parse(response.text.splitlines())
        allowed = parser.can_fetch(user_agent, url)
        note = "robots.txt allows this path" if allowed else "robots.txt disallows this path — document ToS before continuing"
        return RobotsDecision(allowed=allowed, robots_url=robots_url, user_agent=user_agent, note=note)
    except Exception as exc:
        return RobotsDecision(
            allowed=True,
            robots_url=robots_url,
            user_agent=user_agent,
            note=f"robots.txt fetch failed ({exc}); treating as allowed and documenting",
        )
