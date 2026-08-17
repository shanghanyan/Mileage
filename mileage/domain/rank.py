"""§6.1 ranking — pure. No I/O, never reads the clock.

`as_of` is a parameter, not `date.today()`. That is what makes a result
reproducible from (snapshot hash, profile, as_of) and golden tests trivial to
write, and it is the one rule §9 states outright about this module.

Ordering, in strict precedence:

  1. cabin class          First > Business > Premium > Economy
  2. heavy-cash demotion  one boolean, NOT a valuation
  3. fewer points
  4. less cash
  5. faster settlement

Step 2 deserves its own note. Without market fares there is no honest exchange
rate between points and dollars, so this refuses to invent one. But a
60,000-point award carrying $600 of surcharges must not outrank a 70,000-point
award carrying $5. A single demotion tier gets that right without pretending to
a precision we don't have.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Iterable, Optional

from .fuel import HIGH_FUEL_USD
from .models import CLASS_RANK, AwardSpace, PathOption

MAX_RESULTS = 10


def _cabin_rank(option: PathOption) -> int:
    return CLASS_RANK.get(option.cabin, 0)


def sort_key(option: PathOption, *, high_cash_usd: float = HIGH_FUEL_USD) -> tuple:
    return (
        -_cabin_rank(option),                              # 1. cabin dominates
        1 if option.price_paid_usd > high_cash_usd else 0,  # 2. demote heavy cash
        option.source_points,                              # 3. fewer points
        option.price_paid_cents,                           # 4. less cash
        option.settlement_minutes,                         # 5. faster
        option.transfer_hops,                              # 6. fewer moves
        option.label,                                      # stable ordering
    )


def dominates(a: PathOption, b: PathOption) -> bool:
    """True when `a` is the same booking as `b` but strictly better.

    Same currency, same program, same metal, same cabin — so the user is
    choosing between two descriptions of one seat — and `a` costs no more in
    points, cash, or hops, and less in at least one.

    Two things this collapses:

    1. "LifeMiles +15% at 10,870 pts" sitting directly above "LifeMiles at
       12,500 pts". Same seat, same program, strictly more points: the second
       row is not an alternative, it is the first row priced worse, and it was
       eating a top-3 slot something genuinely different could use.

    2. Pointless detours. With airline→airline edges in the graph, Chase →
       Iberia → Qatar → Avios prices identically to Chase → Avios (every hop is
       1:1) and the search happily enumerates every permutation. They are the
       same booking reached by a longer road. Hop count is a real cost, not a
       cosmetic one — transfers are IRREVERSIBLE and each one adds settlement
       time during which award space can vanish — so fewer hops at equal price
       genuinely dominates.
    """
    same_booking = (
        a.kind == b.kind
        and a.currency == b.currency
        and a.program == b.program
        and a.operating_carrier == b.operating_carrier
        and a.cabin == b.cabin
    )
    if not same_booking:
        return False
    no_worse = (
        a.source_points <= b.source_points
        and a.price_paid_cents <= b.price_paid_cents
        and a.transfer_hops <= b.transfer_hops
    )
    strictly_better = (
        a.source_points < b.source_points
        or a.price_paid_cents < b.price_paid_cents
        or a.transfer_hops < b.transfer_hops
    )
    return no_worse and strictly_better


def collapse_dominated(options: Iterable[PathOption]) -> list[PathOption]:
    """Drop rows another row strictly beats, keeping the survivor annotated."""
    options = list(options)  # iterated several times below
    kept: list[PathOption] = []
    for candidate in options:
        if any(dominates(other, candidate) for other in options if other is not candidate):
            continue
        kept.append(candidate)

    # Tell the user a variant was folded in, rather than silently dropping it.
    # Recorded as a FLAG, not by overwriting `reason` — `explain()` builds the
    # reason later and would be skipped entirely if this filled it in first,
    # costing the row its points/cash/availability line.
    out: list[PathOption] = []
    for opt in kept:
        folded = sum(
            1 for other in options if other is not opt and dominates(opt, other)
        )
        if folded:
            out.append(
                replace(
                    opt,
                    flags=sorted(
                        set(opt.flags) | {f"collapsed_variants:{folded + 1}"}
                    ),
                )
            )
        else:
            out.append(opt)
    return out


def explain(option: PathOption, *, as_of: Optional[date] = None) -> str:
    """One line saying why this row is where it is.

    The sweep review could not reconstruct `best` vs `tentative_best` from 12
    days of output — meaning a user could not either. Whatever the rule is, it
    gets stated.
    """
    bits: list[str] = []
    bits.append(f"{option.source_points:,} pts")
    bits.append(f"${option.price_paid_usd:,.0f} out of pocket")
    if option.operating_carrier:
        bits.append(f"on {option.carrier_name or option.operating_carrier} metal")
    if option.price_paid_usd > HIGH_FUEL_USD:
        bits.append(
            f"demoted: cash outlay above ${HIGH_FUEL_USD:,.0f}"
            + (f" ({option.fuel_policy})" if option.fuel_policy else "")
        )
    if option.space is AwardSpace.UNKNOWN:
        bits.append("availability not checked")
    elif option.space is AwardSpace.NONE:
        bits.append("checked — no seats found")
    elif option.space is AwardSpace.CONFIRMED:
        bits.append("seats confirmed")
    if option.transfer_hops > 1:
        bits.append(f"{option.transfer_hops} transfers")
    for flag in option.flags:
        if flag.startswith("collapsed_variants:"):
            bits.append(f"best of {flag.split(':', 1)[1]} variants of this booking")
    if option.settlement_minutes:
        bits.append(f"~{option.settlement_minutes // 60}h to settle")
    for gate in option.gates:
        bits.append(gate.describe())
    if not option.affordable:
        bits.append("more points than you hold")
    return " · ".join(bits)


def rank_options(
    options: Iterable[PathOption],
    *,
    as_of: Optional[date] = None,
    balance_by_currency: Optional[dict[str, int]] = None,
    high_cash_usd: float = HIGH_FUEL_USD,
    limit: int = MAX_RESULTS,
    drop_unaffordable: bool = True,
) -> list[PathOption]:
    """Affordability filter → collapse dominated → §6.1 sort → top `limit`.

    Affordability runs FIRST because §6.1's cabin-dominates rule is only safe
    once unbookable rows are gone: otherwise a first-class route the user cannot
    pay for would outrank every business-class route they can.
    """
    opts = list(options)

    if balance_by_currency is not None:
        opts = [
            replace(
                o,
                affordable=o.source_points
                <= balance_by_currency.get(o.currency or "", 0),
            )
            for o in opts
        ]
    if drop_unaffordable:
        opts = [o for o in opts if o.affordable]

    opts = collapse_dominated(opts)
    opts.sort(key=lambda o: sort_key(o, high_cash_usd=high_cash_usd))
    opts = opts[:limit]
    return [replace(o, reason=o.reason or explain(o, as_of=as_of)) for o in opts]


def partition_gated(
    options: Iterable[PathOption],
    *,
    held_cards: Optional[frozenset[str]] = None,
    account_age_days: Optional[dict[str, int]] = None,
) -> tuple[list[PathOption], list[PathOption]]:
    """Split into (open, gated). §6.3: gated routes are NEVER hidden.

    They come back as a separate list so the UI can show "3 routes require a
    Sapphire card ›" above the results. Telling someone what a card is worth on
    a trip they are actually trying to take is useful output, not a filter.
    """
    cards = held_cards if held_cards is not None else frozenset()
    ages = account_age_days or {}
    open_rows: list[PathOption] = []
    gated_rows: list[PathOption] = []
    for o in options:
        blocking = [g for g in o.gates if not g.satisfied_by(cards, ages)]
        if blocking:
            # Carry ONLY the gates that actually block. A gated row listing
            # requirements the user already meets reads as "you need all of
            # this" when they need one of it.
            gated_rows.append(replace(o, gates=blocking))
        else:
            open_rows.append(o)
    return open_rows, gated_rows


__all__ = [
    "MAX_RESULTS",
    "collapse_dominated",
    "dominates",
    "explain",
    "partition_gated",
    "rank_options",
    "sort_key",
]
