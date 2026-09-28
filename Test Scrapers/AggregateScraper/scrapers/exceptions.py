"""Scraper-specific exceptions — defined before any scraper imports them."""


class SelectorMissError(Exception):
    """A required CSS/XPath selector matched nothing in the response."""


class AmbiguousCellError(Exception):
    """Cell text couldn't be parsed to an unambiguous integer."""


class BlockDetectedError(Exception):
    """Response body contains bot-detection signatures."""


class RateLimitError(Exception):
    """HTTP 429 or equivalent rate-limit response."""


class WaybackSnapshotMissingError(Exception):
    """Wayback API responded OK but no archived snapshot exists for the URL."""
