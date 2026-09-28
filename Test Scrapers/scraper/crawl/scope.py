"""URL scope rules, blocklist, and per-domain page limits."""

from __future__ import annotations

import re
from urllib.parse import urlparse, urljoin, urldefrag


def normalize_url(url: str) -> str:
    url, _ = urldefrag(url.strip())
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return f"{parsed.scheme}://{netloc}{path}" + (f"?{parsed.query}" if parsed.query else "")


def registrable_domain(host: str) -> str:
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # Handle multi-part TLDs heuristically (co.jp, com.au)
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


class ScopeManager:
    def __init__(self, scope_config: dict, blocklist: list[str], aggregator_domains: list[str]):
        self.scope = {k.lower(): v for k, v in scope_config.items()}
        self.blocklist = [b.lower() for b in blocklist]
        self.aggregator_domains = [d.lower() for d in aggregator_domains]
        self._pages_per_domain: dict[str, int] = {}
        self._depth_by_url: dict[str, int] = {}

    def seed_depth(self, url: str) -> None:
        self._depth_by_url[normalize_url(url)] = 0

    def depth_of(self, url: str) -> int:
        return self._depth_by_url.get(normalize_url(url), 0)

    def child_depth(self, parent_url: str) -> int:
        return self.depth_of(parent_url) + 1

    def register_visit(self, url: str) -> None:
        domain = self._domain_key(url)
        self._pages_per_domain[domain] = self._pages_per_domain.get(domain, 0) + 1

    def pages_for_domain(self, url: str) -> int:
        return self._pages_per_domain.get(self._domain_key(url), 0)

    def _domain_key(self, url: str) -> str:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        # Longest suffix match (e.g. creditcards.chase.com before chase.com)
        matched = None
        for scope_domain in sorted(self.scope.keys(), key=len, reverse=True):
            if host == scope_domain or host.endswith("." + scope_domain):
                matched = scope_domain
                break
        return matched if matched else registrable_domain(host)

    def scope_for_url(self, url: str) -> dict | None:
        key = self._domain_key(url)
        return self.scope.get(key)

    def is_blocked(self, url: str) -> str | None:
        lower = url.lower()
        for pattern in self.blocklist:
            if pattern in lower:
                return f"blocklist:{pattern}"
        return None

    def _path_allowed(self, url: str, scope: dict) -> str | None:
        prefix = scope.get("path_prefix")
        if not prefix:
            return None
        path = urlparse(url).path.lower()
        if not path.startswith(prefix.lower()):
            return f"path must start with {prefix}"
        return None

    def can_enqueue(self, url: str, parent_url: str | None = None) -> tuple[bool, str]:
        norm = normalize_url(url)
        blocked = self.is_blocked(norm)
        if blocked:
            return False, blocked

        parsed = urlparse(norm)
        if parsed.scheme not in ("http", "https"):
            return False, "non-http scheme"

        scope = self.scope_for_url(norm)
        if scope is None:
            return False, "out of scope domain"

        path_reason = self._path_allowed(norm, scope)
        if path_reason:
            return False, path_reason

        depth = self.child_depth(parent_url) if parent_url else self.depth_of(norm)
        if depth > scope.get("max_depth", 3):
            return False, f"max depth {scope['max_depth']} exceeded"

        if self.pages_for_domain(norm) >= scope.get("max_pages", 100):
            return False, f"max pages {scope['max_pages']} for domain"

        return True, "ok"

    def can_visit(self, url: str) -> tuple[bool, str]:
        norm = normalize_url(url)
        blocked = self.is_blocked(norm)
        if blocked:
            return False, blocked
        scope = self.scope_for_url(norm)
        if scope is None:
            return False, "out of scope domain"
        path_reason = self._path_allowed(norm, scope)
        if path_reason:
            return False, path_reason
        depth = self.depth_of(norm)
        if depth > scope.get("max_depth", 3):
            return False, f"max depth exceeded"
        if self.pages_for_domain(norm) >= scope.get("max_pages", 100):
            return False, f"max pages for domain"
        return True, "ok"

    def is_aggregator(self, url: str) -> bool:
        key = self._domain_key(url)
        return key in self.aggregator_domains

    def same_scope_domain(self, link: str, base_url: str) -> bool:
        return self.scope_for_url(link) is not None and self._domain_key(link) == self._domain_key(base_url)


def extract_links(html: str, base_url: str) -> list[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        links.append(absolute)
    return links


def discover_sitemap_urls(domain: str, session: "requests.Session | None" = None) -> list[str]:
    import requests

    sess = session or requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (compatible; TravelScraper/1.0)"})
    candidates = [
        f"https://www.{domain}/sitemap.xml",
        f"https://{domain}/sitemap.xml",
    ]
    found: list[str] = []
    for sm_url in candidates:
        try:
            resp = sess.get(sm_url, timeout=15)
            if resp.status_code != 200:
                continue
            locs = re.findall(r"<loc>(.*?)</loc>", resp.text, re.I)
            found.extend(normalize_url(u) for u in locs[:200])
            if found:
                break
        except Exception:
            continue
    return found
