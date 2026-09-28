from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def _format_data_age(days: int | None) -> str:
    if days is None:
        return "—"
    suffix = ""
    if days > 120:
        suffix = " [red]⚠[/red]"
    elif days > 60:
        suffix = " [yellow]⚠[/yellow]"
    return f"{days}d{suffix}"


def render_leaderboard(
    results: list[dict],
    origin: str,
    dest: str,
    cabin: str,
    cash: float,
    fees: float,
    portal_cpp: float,
    conclusion: dict,
    source_ages: dict[str, int] | None = None,
    cache_warnings: list[str] | None = None,
) -> None:
    header = (
        f"C1 Miles Optimizer · {origin} → {dest} · {cabin.title()} · "
        f"${cash:,.0f} cash fare"
    )
    if fees > 0:
        header += f" (fees ${fees:,.0f})"

    table = Table(title=header, show_header=True, header_style="bold cyan", show_lines=True)
    table.add_column("Rank", justify="right", style="dim")
    table.add_column("Path")
    table.add_column("C1 Miles", justify="right")
    table.add_column("Ptnr Miles", justify="right")
    table.add_column("CPP", justify="right", style="green")
    table.add_column("Sources", justify="center")
    table.add_column("Data age", justify="center")
    table.add_column("Carrier")

    rank = 0
    for r in results:
        if r.get("method") == "hidden":
            continue
        rank += 1
        partner_miles = f"{r['partner_miles']:,}" if r.get("partner_miles") else "—"
        c1_display = f"{r['c1_miles']:,}"
        if r.get("non_1to1"):
            c1_display += "*"
        cpp_str = r.get("cpp_display", f"{r['cpp']:.2f}¢")
        label = r["path"]
        if r.get("method") == "portal":
            label = f"C1 Portal (Venture X @ {portal_cpp}¢)"
        table.add_row(
            str(rank),
            label,
            c1_display,
            partner_miles,
            cpp_str,
            str(r.get("sources", "—")),
            _format_data_age(r.get("data_age_days")),
            r.get("carrier", "—"),
        )

    console.print(table)

    flag_lines: list[str] = []
    if cache_warnings:
        flag_lines.extend(cache_warnings)

    for r in results:
        if r.get("method") == "hidden":
            flag_lines.append(r["path"])
            continue
        for f in r.get("flags", []):
            flag_lines.append(f"  {r['path']}: {f}")

    stale_programs = [
        r["path"] for r in results
        if r.get("stale") and r.get("method") not in ("portal", "hidden")
    ]
    if stale_programs:
        flag_lines.append(
            f"⚠  Chart data may be stale for: {', '.join(stale_programs)} — re-scrape recommended"
        )

    if source_ages:
        age_parts = [f"{name} ({days}d)" for name, days in sorted(source_ages.items())]
        flag_lines.append(f"Sources: {', '.join(age_parts)}")

    if flag_lines:
        console.print(Panel("\n".join(flag_lines), title="Notes", border_style="yellow"))

    if any(r.get("non_1to1") for r in results):
        console.print("[dim]* C1 miles reflects non-1:1 transfer ratio[/dim]")

    verdict_style = {
        "best": "green",
        "tentative_best": "yellow",
        "comparable": "blue",
        "portal_only": "dim",
        "insufficient_data": "red",
    }.get(conclusion.get("verdict", ""), "white")

    console.print()
    console.print(
        Panel(conclusion.get("message", ""), title="Conclusion", border_style=verdict_style)
    )


def render_freshness_report(entries: list[dict]) -> None:
    table = Table(title="Data Freshness Report", show_header=True, header_style="bold")
    table.add_column("Program")
    table.add_column("Source")
    table.add_column("Age", justify="right")
    table.add_column("Trust", justify="right")
    table.add_column("Used", justify="center")

    for entry in entries:
        status = entry.get("status", "unknown")
        style = "red" if status == "stale" else ("yellow" if status == "aging" else "green")
        age = entry.get("age_days")
        age_str = f"{age}d" if age is not None else "—"
        used = "✓" if entry.get("used") else ""
        if entry.get("source") == "fallback_rates.json":
            used = "✓ ⚠"
        trust = entry.get("trust")
        trust_str = f"{trust:.2f}" if trust is not None else "—"
        table.add_row(
            entry["name"],
            entry.get("source", "—")[:50],
            f"[{style}]{age_str}[/{style}]",
            trust_str,
            used,
        )
    console.print(table)


def render_alerts(alerts: list) -> None:
    if not alerts:
        console.print("[green]No devaluation alerts detected.[/green]")
        return
    table = Table(title="Devaluation Alerts", show_header=True, header_style="bold red")
    table.add_column("Feed")
    table.add_column("Title")
    table.add_column("Programs")
    for alert in alerts:
        table.add_row(alert.feed_name, alert.title[:80], ", ".join(alert.programs))
    console.print(table)
