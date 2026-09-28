"""Detect point conversion charts in HTML (BeautifulSoup + lxml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup, Tag
from lxml import html as lxml_html

NUMBER_RE = re.compile(r"\d")
RATIO_RE = re.compile(
    r"\d+\s*[:/]\s*\d+"
    r"|\d+(?:\.\d+)?\s*(?:x|miles?|pts?|points?)"
    r"|\d[\d,]*\s*(?:points?|pts?|miles?|mr)?\s*=\s*\d",
    re.I,
)
CONVERSION_LINE_RE = re.compile(
    r"\d+\s*[:/]\s*\d+"
    r"|\d[\d,]*\s*(?:points?|pts?|miles?|mr)?\s*=\s*\d"
    r"|\d+(?:\.\d+)?\s*(?:x)\s+(?:miles?|points?)"
    r"|\d[\d,]*\s+(?:capital one )?(?:miles?|points?)\s+convert"
    r"|\d+\s*%\s*bonus\s+miles",
    re.I,
)


@dataclass
class ChartResult:
    found: bool
    confidence: float
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    lxml_path: str = ""
    table_index: int | None = None
    source: str = ""
    keyword_hits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "confidence": round(self.confidence, 4),
            "headers": self.headers,
            "rows": self.rows,
            "lxml_path": self.lxml_path,
            "table_index": self.table_index,
            "source": self.source,
            "keyword_hits": self.keyword_hits,
        }


def _cell_text(cell: Tag) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def _table_matrix(table: Tag) -> tuple[list[str], list[list[str]]]:
    headers: list[str] = []
    rows: list[list[str]] = []
    thead = table.find("thead")
    if thead:
        header_row = thead.find("tr")
        if header_row:
            headers = [_cell_text(c) for c in header_row.find_all(["th", "td"])]
    body_rows = table.find_all("tr")
    for i, tr in enumerate(body_rows):
        cells = [_cell_text(c) for c in tr.find_all(["th", "td"])]
        if not any(cells):
            continue
        if not headers and i == 0 and tr.find("th"):
            headers = cells
            continue
        if headers and cells == headers:
            continue
        rows.append(cells)
    if not headers and rows:
        maybe = rows[0]
        if any(not NUMBER_RE.search(c) for c in maybe):
            headers = maybe
            rows = rows[1:]
    return headers, rows


def _dl_matrix(dl: Tag) -> tuple[list[str], list[list[str]]]:
    rows: list[list[str]] = []
    current_term = ""
    for child in dl.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "dt":
            current_term = _cell_text(child)
        elif child.name == "dd":
            value = _cell_text(child)
            if not value:
                continue
            rows.append([current_term, value] if current_term else [value])
    return (["Partner", "Conversion"] if rows else []), rows


def _list_matrix(list_tag: Tag) -> tuple[list[str], list[list[str]]]:
    items = [_cell_text(li) for li in list_tag.find_all("li", recursive=False)]
    items = [item for item in items if item]
    if len(items) < 2:
        return [], []
    rows: list[list[str]] = []
    two_col = False
    for item in items:
        ratio_split = re.match(
            r"^(\d+\s*[:/]\s*\d+(?:\.\d+)?(?:\s*ratio)?)\s*:\s*(.+)$",
            item,
            re.I,
        )
        if ratio_split:
            rows.append([ratio_split.group(1).strip(), ratio_split.group(2).strip()])
            two_col = True
            continue
        if ":" in item[:48] and not re.match(r"^\d+\s*[:/]\s*\d+", item):
            left, right = item.split(":", 1)
            if left.strip() and right.strip():
                rows.append([left.strip(), right.strip()])
                two_col = True
                continue
        rows.append([item])
    headers = ["Item", "Detail"] if two_col else ["Item"]
    return headers, rows


def _lxml_path_for_element(root: Any, element: Any) -> str:
    """Build a simple /tag[n] path from an lxml element."""
    parts: list[str] = []
    node = element
    while node is not None and node is not root.getroottree().getroot() if hasattr(root, "getroottree") else True:
        if not hasattr(node, "tag") or not isinstance(node.tag, str):
            break
        parent = node.getparent()
        if parent is None:
            parts.append(node.tag)
            break
        siblings = [c for c in parent if hasattr(c, "tag") and c.tag == node.tag]
        idx = siblings.index(node) + 1 if node in siblings else 1
        parts.append(f"{node.tag}[{idx}]")
        node = parent
    parts.reverse()
    return "/" + "/".join(parts) if parts else ""


def lxml_path_for_table_index(html: str, table_index: int) -> str:
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return ""
    tables = tree.xpath("//table")
    if table_index < 0 or table_index >= len(tables):
        return ""
    return _lxml_path_for_element(tree, tables[table_index])


def lxml_path_for_xpath(html: str, xpath: str) -> str:
    try:
        tree = lxml_html.fromstring(html)
        found = tree.xpath(xpath)
    except Exception:
        return ""
    if not found:
        return ""
    return _lxml_path_for_element(tree, found[0])


def _score_table(
    headers: list[str],
    rows: list[list[str]],
    chart_keywords: list[str],
) -> tuple[float, list[str]]:
    if not rows:
        return 0.0, []
    blob = " ".join(headers + [c for row in rows for c in row]).lower()
    hits = [kw for kw in chart_keywords if kw.lower() in blob]
    keyword_score = min(len(hits) / max(len(chart_keywords), 1), 1.0)
    numeric_cells = sum(1 for row in rows for c in row if NUMBER_RE.search(c))
    total_cells = max(sum(len(row) for row in rows), 1)
    numeric_score = min(numeric_cells / total_cells, 1.0)
    size_score = min(len(rows) / 6.0, 1.0) * 0.5 + min(
        max(len(headers), max((len(r) for r in rows), default=0)) / 4.0, 1.0
    ) * 0.5
    ratio_bonus = 0.15 if RATIO_RE.search(blob) else 0.0
    col_bonus = 0.1 if max(len(headers), max((len(r) for r in rows), default=0)) >= 2 else 0.0
    confidence = (
        0.45 * keyword_score
        + 0.25 * numeric_score
        + 0.15 * size_score
        + ratio_bonus
        + col_bonus
    )
    if len(hits) >= 2 and numeric_cells >= 2 and len(rows) >= 2:
        confidence = max(confidence, 0.5)
    if (
        len(rows) >= 4
        and any(token in blob for token in ("convert", "conversion", "transfer"))
        and any(token in blob for token in ("mile", "miles", "point", "points"))
    ):
        confidence = max(confidence, 0.5)
    return min(confidence, 1.0), hits


def _looks_like_nav(headers: list[str], rows: list[list[str]]) -> bool:
    if not rows:
        return True
    blob = " ".join(headers + [c for row in rows for c in row]).lower()
    if RATIO_RE.search(blob) or CONVERSION_LINE_RE.search(blob):
        return False
    if NUMBER_RE.search(blob) and any(token in blob for token in ("mile", "point", "convert")):
        return False
    if len(rows) >= 4 and any(token in blob for token in ("convert", "conversion", "transfer")):
        return False
    return True


def _candidate(
    headers: list[str],
    rows: list[list[str]],
    chart_keywords: list[str],
    *,
    source: str,
    table_index: int | None,
    lxml_path: str,
) -> ChartResult | None:
    if not rows:
        return None
    if source != "table" and _looks_like_nav(headers, rows):
        return None
    confidence, hits = _score_table(headers, rows, chart_keywords)
    return ChartResult(
        found=confidence >= 0.45,
        confidence=confidence,
        headers=headers,
        rows=rows[:80],
        lxml_path=lxml_path,
        table_index=table_index,
        source=source,
        keyword_hits=hits,
    )


def _text_line_chart(soup: BeautifulSoup, chart_keywords: list[str]) -> ChartResult | None:
    raw_lines = [re.sub(r"\s+", " ", ln).strip() for ln in soup.get_text("\n", strip=True).splitlines()]
    lines = [ln for ln in raw_lines if ln]
    clusters: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if CONVERSION_LINE_RE.search(line) or RATIO_RE.search(line):
            if 8 <= len(line) <= 280:
                current.append(line)
            continue
        if len(current) >= 2:
            clusters.append(current)
        current = []
    if len(current) >= 2:
        clusters.append(current)
    best: ChartResult | None = None
    for cluster in clusters:
        rows = [[line] for line in cluster]
        candidate = _candidate(
            ["Conversion"],
            rows,
            chart_keywords,
            source="text",
            table_index=None,
            lxml_path="",
        )
        if candidate and (best is None or candidate.confidence > best.confidence):
            best = candidate
    return best


def detect_chart(html: str, chart_keywords: list[str]) -> ChartResult:
    """Find the highest-confidence conversion table/grid in the page HTML."""
    soup = BeautifulSoup(html, "lxml")
    best: ChartResult | None = None

    def consider(candidate: ChartResult | None) -> None:
        nonlocal best
        if candidate is None:
            return
        if best is None or candidate.confidence > best.confidence:
            best = candidate

    for index, table in enumerate(soup.find_all("table")):
        headers, rows = _table_matrix(table)
        consider(
            _candidate(
                headers,
                rows,
                chart_keywords,
                source="table",
                table_index=index,
                lxml_path=lxml_path_for_table_index(html, index),
            )
        )

    conversion_dl_rows: list[list[str]] = []
    for index, dl in enumerate(soup.find_all("dl")):
        headers, rows = _dl_matrix(dl)
        consider(
            _candidate(
                headers,
                rows,
                chart_keywords,
                source="dl",
                table_index=index,
                lxml_path=lxml_path_for_xpath(html, f"(//dl)[{index + 1}]"),
            )
        )
        for row in rows:
            blob = " ".join(row)
            if NUMBER_RE.search(blob) and (RATIO_RE.search(blob) or "=" in blob):
                conversion_dl_rows.append(row if len(row) == 2 else [row[0], ""])

    if len(conversion_dl_rows) >= 3:
        consider(
            _candidate(
                ["Partner", "Conversion"],
                conversion_dl_rows,
                chart_keywords,
                source="dl_group",
                table_index=0,
                lxml_path=lxml_path_for_xpath(html, "(//dl)[1]"),
            )
        )

    for index, list_tag in enumerate(soup.find_all(["ul", "ol"])):
        headers, rows = _list_matrix(list_tag)
        consider(
            _candidate(
                headers,
                rows,
                chart_keywords,
                source="list",
                table_index=index,
                lxml_path=lxml_path_for_xpath(html, f"(//{list_tag.name})[{index + 1}]"),
            )
        )

    consider(_text_line_chart(soup, chart_keywords))

    if best is None:
        text = soup.get_text(" ", strip=True).lower()
        hits = [kw for kw in chart_keywords if kw.lower() in text]
        confidence = min(len(hits) / max(len(chart_keywords), 1) * 0.4, 0.4)
        return ChartResult(
            found=False,
            confidence=confidence,
            keyword_hits=hits,
            source="none",
        )
    return best
