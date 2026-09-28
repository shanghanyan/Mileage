import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import networkx as nx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from optimizer.models import (
    Currency, TransferEdge, RedemptionPath, UserPortfolio,
    HoldingValuation, OptimizationResult
)

# One-way (irreversible) conversions — e.g. cashback → miles — keep their raw
# face-value CPP for display, but are demoted in the ranking so they never sit
# above an equivalent reversible redemption. ONE_WAY_PENALTY additionally scales
# their effective score so a marginal irreversible edge isn't worth locking in.
ONE_WAY_PENALTY = float(os.getenv("ONE_WAY_PENALTY", "0.5"))


class PointOptimizer:
    def __init__(self, graph: nx.MultiDiGraph, portfolio: UserPortfolio) -> None:
        self.G = graph
        self.portfolio = portfolio

    def find_all_paths(self) -> list[RedemptionPath]:
        all_paths: list[RedemptionPath] = []
        sources = [
            (Currency.C1_MILES,      "C1 Miles"),
            (Currency.C1_CASHBACK,   "C1 Cashback"),
            (Currency.LIFEMILES,     "LifeMiles"),
            (Currency.TURKISH_MILES, "Turkish M&S"),
            (Currency.KRISFLYER,     "KrisFlyer"),
            (Currency.AEROPLAN,      "Aeroplan"),
        ]

        for src_currency, _ in sources:
            if not self.G.has_node(src_currency):
                continue

            for node_path in nx.all_simple_paths(
                self.G, src_currency, Currency.USD, cutoff=4
            ):
                for edge_combo in self._edge_combinations(node_path):
                    path = self._build_path(node_path, edge_combo, src_currency)
                    if path:
                        all_paths.append(path)

        # Multiple sources (static_config, capital_one_scraper, turkish_scraper, …)
        # can emit the same transfer edge, and parallel edges differ only in label
        # formatting ("1:1" vs "1.00:1"). Dedup across all sources after building
        # every path so the ranked list contains genuinely distinct routes.
        ranked = sorted(all_paths, key=self._rank_key)
        return self._dedupe_paths(ranked)

    @staticmethod
    def _rank_key(path: RedemptionPath) -> tuple:
        """Sort key: reversible paths first, then by penalty-adjusted CPP (desc).

        Returning a tuple keeps reversible redemptions ahead of irreversible
        cashback→miles conversions regardless of their inflated face CPP, which
        is what stops one-way paths from monopolising the top of the ranking.
        """
        effective_cpp = path.total_cpp * (ONE_WAY_PENALTY if path.is_one_way else 1.0)
        return (1 if path.is_one_way else 0, -effective_cpp)

    @staticmethod
    def _normalize_ratio_str(text: str) -> str:
        """Collapse equivalent ratio formatting: '1.00:1' → '1:1', '2.00:1.5' → '2:1.5'."""
        def repl(m: "re.Match") -> str:
            out = []
            for part in (m.group(1), m.group(2)):
                f = float(part)
                out.append(str(int(f)) if f == int(f) else part)
            return ":".join(out)

        return re.sub(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", repl, text)

    def _path_key(self, path: RedemptionPath) -> tuple:
        """
        Canonical identity for a path, independent of which scraper produced an
        edge or how its ratio was stringified. Two paths are the same redemption
        when they share the same currency chain, per-hop value (edge_cpp), and
        ratio-normalized labels.
        """
        hop_keys = tuple(
            (
                h.from_currency.value,
                h.to_currency.value,
                round(h.edge_cpp, 4),
                self._normalize_ratio_str(h.label),
            )
            for h in path.hops
        )
        return (path.source_start.value, hop_keys)

    def _dedupe_paths(self, paths: list[RedemptionPath]) -> list[RedemptionPath]:
        seen: set[tuple] = set()
        unique: list[RedemptionPath] = []
        for path in paths:
            key = self._path_key(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    def _edge_combinations(self, node_path: list) -> list[tuple]:
        import itertools
        per_hop = []
        for u, v in zip(node_path[:-1], node_path[1:]):
            hop_edges = [self.G[u][v][k] for k in self.G[u][v]]
            per_hop.append(hop_edges)
        return list(itertools.product(*per_hop))

    def _build_path(
        self,
        node_path: list[Currency],
        edge_combo: tuple[dict],
        src_currency: Currency,
    ) -> Optional[RedemptionPath]:
        total_cpp = 1.0
        for edge_data in edge_combo:
            total_cpp *= edge_data["edge_cpp"]

        if total_cpp <= 0:
            return None

        node_labels = [n.value for n in node_path]
        label_parts = []
        for i, edge_data in enumerate(edge_combo):
            if i == 0:
                label_parts.append(node_labels[0])
            label_parts.append(f"→ {edge_data['label']}")
        path_label = " ".join(label_parts)

        hops = [
            TransferEdge(
                from_currency=node_path[i],
                to_currency=node_path[i + 1],
                ratio=e["ratio"],
                edge_cpp=e["edge_cpp"],
                label=e["label"],
                source_name=e["source_name"],
                source_url=e["source_url"],
                scraped_at=datetime.fromisoformat(e["scraped_at"]),
                is_one_way=e.get("is_one_way", False),
                stale=e.get("stale", False),
                suspicious=e.get("suspicious", False),
            )
            for i, e in enumerate(edge_combo)
        ]

        return RedemptionPath(
            path_label=path_label,
            hops=hops,
            total_cpp=round(total_cpp, 4),
            source_start=src_currency,
            is_one_way=any(h.is_one_way for h in hops),
            has_stale=any(h.stale for h in hops),
            has_suspicious=any(h.suspicious for h in hops),
        )

    def value_portfolio(self, all_paths: list[RedemptionPath]) -> list[HoldingValuation]:
        holdings = [
            (Currency.C1_MILES,      self.portfolio.c1_miles),
            (Currency.C1_CASHBACK,   self.portfolio.c1_cashback_usd),
            (Currency.LIFEMILES,     self.portfolio.lifemiles),
            (Currency.TURKISH_MILES, self.portfolio.turkish_miles),
            (Currency.KRISFLYER,     self.portfolio.krisflyer_miles),
            (Currency.AEROPLAN,      self.portfolio.aeroplan_miles),
        ]
        valuations: list[HoldingValuation] = []
        for currency, amount in holdings:
            if amount <= 0:
                continue
            candidate_paths = [p for p in all_paths if p.source_start == currency]
            if currency == Currency.C1_CASHBACK:
                # One-way cashback→miles paths inflate compounded CPP; prefer reversible paths.
                reversible = [p for p in candidate_paths if not p.is_one_way]
                if reversible:
                    candidate_paths = reversible
            if not candidate_paths:
                continue
            best = candidate_paths[0]
            value_usd = amount * best.total_cpp / 100
            valuations.append(HoldingValuation(
                currency=currency,
                amount=amount,
                best_path=best,
                value_usd=round(value_usd, 2),
            ))
        return valuations

    def display_results(self, result: OptimizationResult) -> None:
        console = Console()
        p = result.portfolio
        portfolio_line = (
            f"C1 Miles: {p.c1_miles:,.0f}  |  Cashback: ${p.c1_cashback_usd:,.2f}  |  "
            f"Turkish: {p.turkish_miles:,.0f}  |  LifeMiles: {p.lifemiles:,.0f}  |  "
            f"KrisFlyer: {p.krisflyer_miles:,.0f}  |  Aeroplan: {p.aeroplan_miles:,.0f}"
        )
        console.print(Panel(portfolio_line, title="[bold]Portfolio", border_style="blue"))

        val_table = Table(title="Holding Valuations", show_header=True, header_style="bold cyan")
        val_table.add_column("Currency", style="white")
        val_table.add_column("Amount", justify="right")
        val_table.add_column("Best Path", style="dim")
        val_table.add_column("CPP", justify="right", style="green")
        val_table.add_column("Value (USD)", justify="right", style="bold green")
        for hv in result.holding_valuations:
            flags = ""
            if hv.best_path.is_one_way:     flags += " ⚠️ONE-WAY"
            if hv.best_path.has_stale:      flags += " ⚠️STALE"
            if hv.best_path.has_suspicious: flags += " ⚠️CHECK"
            val_table.add_row(
                hv.currency.value,
                f"{hv.amount:,.0f}",
                hv.best_path.path_label + flags,
                f"{hv.best_path.total_cpp:.3f}¢",
                f"${hv.value_usd:,.2f}",
            )
        val_table.add_row(
            "[bold]TOTAL", "", "", "", f"[bold]${result.total_value_usd:,.2f}"
        )
        console.print(val_table)

        path_table = Table(title="All Redemption Paths (top 12)", show_header=True, header_style="bold magenta")
        path_table.add_column("Rank", justify="right", style="dim")
        path_table.add_column("Path")
        path_table.add_column("CPP", justify="right", style="green")
        path_table.add_column("Source", style="dim")
        path_table.add_column("Flags")
        for i, path in enumerate(result.all_paths[:12], 1):
            star = "★ " if path.recommended else ("★ " if i == 1 else "  ")
            flags = []
            if path.is_one_way:     flags.append("ONE-WAY")
            if path.has_stale:      flags.append("STALE DATA")
            if path.has_suspicious: flags.append("VERIFY NUMBERS")
            sources = {h.source_name for h in path.hops}
            path_table.add_row(
                f"{star}{i}",
                path.path_label,
                f"{path.total_cpp:.3f}¢",
                ", ".join(sources),
                " | ".join(flags) if flags else "—",
            )
        console.print(path_table)

        warnings = []
        for path in result.all_paths:
            if path.has_stale:
                warnings.append(f"⚠️  [{path.path_label}] uses potentially stale rates — re-run scraper")
            if path.is_one_way:
                warnings.append(f"⚠️  [{path.path_label}] is ONE-WAY — cashback→miles cannot be reversed")
        if warnings:
            unique = list(dict.fromkeys(warnings))
            console.print(Panel("\n".join(unique), title="[bold yellow]Warnings", border_style="yellow"))

    def save_json(self, result: OptimizationResult) -> str:
        Path("logs").mkdir(exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = f"logs/optimization_result_{ts}.json"
        with open(path, "w") as f:
            json.dump(result.model_dump(mode="json"), f, indent=2, default=str)
        return path
