"""Mode 1: DOM / accessibility tree + HTML structured parsing."""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup


def _ax_text(node: dict | None, parts: list[str]) -> None:
    if not node:
        return
    name = node.get("name")
    value = node.get("value")
    role = node.get("role", "")
    if name:
        parts.append(f"{role}:{name}")
    if value:
        parts.append(str(value))
    for child in node.get("children") or []:
        _ax_text(child, parts)


def parse_ax_tree(snapshot: dict | None) -> dict[str, Any]:
    if not snapshot:
        return {}
    parts: list[str] = []
    _ax_text(snapshot, parts)
    text = " ".join(parts)
    return {"ax_text": text[:50000], "ax_node_count": len(parts)}


def _json_ld_objects(soup: BeautifulSoup) -> list[dict]:
    objects: list[dict] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                objects.extend(x for x in data if isinstance(x, dict))
            elif isinstance(data, dict):
                objects.append(data)
        except (json.JSONDecodeError, TypeError):
            continue
    return objects


def _meta_tags(soup: BeautifulSoup) -> dict[str, str]:
    meta: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = tag.get("property") or tag.get("name")
        content = tag.get("content")
        if key and content:
            meta[key] = content
    return meta


def _tables(soup: BeautifulSoup) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    for table in soup.find_all("table")[:20]:
        rows: list[list[str]] = []
        for tr in table.find_all("tr")[:100]:
            cells = [
                c.get_text(" ", strip=True)
                for c in tr.find_all(["th", "td"])
            ]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _definition_lists(soup: BeautifulSoup) -> dict[str, str]:
    dls: dict[str, str] = {}
    for dl in soup.find_all("dl")[:30]:
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            k = dt.get_text(" ", strip=True)
            v = dd.get_text(" ", strip=True)
            if k:
                dls[k] = v
    return dls


IATA_RE = re.compile(r"\b([A-Z]{3})\b")
MILES_RE = re.compile(r"([\d,]+)\s*(?:miles|points)", re.I)
USD_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")


def _heuristic_fields(text: str, url: str) -> dict[str, Any]:
    """Lightweight pattern extraction from page text."""
    out: dict[str, Any] = {}
    iatas = IATA_RE.findall(text.upper())
    if len(iatas) >= 2:
        out["origin"] = iatas[0]
        out["destination"] = iatas[1]
    miles = MILES_RE.search(text)
    if miles:
        out["miles_cost"] = miles.group(1).replace(",", "")
    usd = USD_RE.search(text)
    if usd:
        out["cash_cost_usd"] = usd.group(1).replace(",", "")
    if "business" in text.lower():
        out["cabin"] = "business"
    elif "first" in text.lower():
        out["cabin"] = "first"
    elif "premium economy" in text.lower():
        out["cabin"] = "premium_economy"
    elif "economy" in text.lower():
        out["cabin"] = "economy"
    return out


async def extract_dom(page, html: str, url: str) -> tuple[dict[str, Any], bool, str | None]:
    dom_data: dict[str, Any] = {}
    try:
        snapshot = None
        try:
            snapshot = await page.accessibility.snapshot()
        except Exception:
            snapshot = None

        ax = parse_ax_tree(snapshot)
        soup = BeautifulSoup(html, "lxml")
        meta = _meta_tags(soup)
        tables = _tables(soup)
        dls = _definition_lists(soup)
        json_ld = _json_ld_objects(soup)
        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        body_text = soup.get_text(" ", strip=True)[:100000]
        heuristics = _heuristic_fields(body_text + " " + ax.get("ax_text", ""), url)

        dom_data = {
            **heuristics,
            "page_title": title,
            "meta": meta,
            "definition_lists": dls,
            "tables": tables,
            "json_ld": json_ld,
            **ax,
        }

        if _is_challenge_page(body_text, title):
            return {}, False, "cloudflare_or_challenge_page"

        if not body_text and not ax.get("ax_text"):
            return dom_data, False, "empty_page"

        return dom_data, True, None
    except Exception as e:
        return {}, False, str(e)


def _is_challenge_page(body: str, title: str) -> bool:
    markers = (
        "just a moment",
        "checking your browser",
        "cloudflare",
        "access denied",
        "captcha",
    )
    combined = (body + title).lower()[:5000]
    return any(m in combined for m in markers)

