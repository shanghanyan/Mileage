"""Post-scrape inspect-mode keyword hits (BeautifulSoup + lxml paths)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from bs4 import BeautifulSoup, Tag
from lxml import html as lxml_html

from scrapers.contracts import PROJECT_ROOT

KEYWORDS_PATH = PROJECT_ROOT / "keywords.yaml"


def load_keywords(path: Path | None = None) -> dict[str, list[str]]:
    data = yaml.safe_load((path or KEYWORDS_PATH).read_text(encoding="utf-8")) or {}
    return data.get("keywords") or {}


def _element_path(tag: Tag) -> str:
    parts: list[str] = []
    node: Tag | None = tag
    while node and getattr(node, "name", None):
        parent = node.parent
        if parent and getattr(parent, "name", None):
            same = [c for c in parent.find_all(node.name, recursive=False)]
            idx = same.index(node) + 1 if node in same else 1
            parts.append(f"{node.name}[{idx}]")
        else:
            parts.append(node.name)
        node = parent if isinstance(parent, Tag) else None
    parts.reverse()
    return "/" + "/".join(parts)


def inspect_keyword_hits(html: str, keywords: dict[str, list[str]] | None = None, limit: int = 40) -> list[dict[str, Any]]:
    """Find keyword occurrences and attach a coarse lxml-style path."""
    groups = keywords or load_keywords()
    soup = BeautifulSoup(html, "lxml")
    try:
        tree = lxml_html.fromstring(html)
        text_nodes = tree.xpath("//*[self::p or self::td or self::th or self::li or self::h1 or self::h2 or self::h3 or self::span]")
    except Exception:
        text_nodes = []

    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for group, words in groups.items():
        for word in words:
            needle = word.lower()
            # Prefer live lxml nodes when available
            for node in text_nodes:
                raw = (node.text_content() or "").strip()
                if needle not in raw.lower():
                    continue
                snippet = " ".join(raw.split())[:180]
                key = (group, word, snippet)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    {
                        "group": group,
                        "keyword": word,
                        "snippet": snippet,
                        "lxml_path": node.getroottree().getpath(node) if hasattr(node, "getroottree") else "",
                    }
                )
                if len(hits) >= limit:
                    return hits

    if hits:
        return hits[:limit]

    # BeautifulSoup fallback when lxml walk found nothing
    for group, words in groups.items():
        for word in words:
            for tag in soup.find_all(string=lambda s, w=word: s and w.lower() in s.lower()):
                parent = tag.parent if isinstance(tag.parent, Tag) else None
                snippet = " ".join(str(tag).split())[:180]
                key = (group, word, snippet)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(
                    {
                        "group": group,
                        "keyword": word,
                        "snippet": snippet,
                        "lxml_path": _element_path(parent) if parent else "",
                    }
                )
                if len(hits) >= limit:
                    return hits
    return hits
