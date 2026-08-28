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
from .models import CLASS_RANK, AwardSpace, GateKind, PathOption

MAX_RESULTS = 10

# How far past the user's balance a route may sit and still be worth showing.
# 1.5x means "you hold 100k, we will show you a 150k route and name the gap".
#
# Dropping everything above the balance was silent AND wrong in both directions:
# a route needing 4% more points vanished with no trace, while JFK-JNB reported
# "no bookable option" when in fact 8 chart rows priced it and every one landed
# at 105k-115k against a 100k balance. "Nothing exists" and "you are 5k short"
# are opposite answers and were rendering identically.
#
# The cap exists because a 400k route is not advice, it is noise. Everything
# above it is COUNTED, never silently discarded.
REACH_MULTIPLE = 1.5


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
        # Name the gap. "More points than you hold" is true of a 5,000-point
        # shortfall and a 300,000-point one, and the user's next move is
        # completely different in each case.
        if option.shortfall_points:
            bits.append(
                f"{option.shortfall_points:,} pts short — a reach, not unbookable"
            )
        else:
            bits.append("more points than you hold")
    return " · ".join(bits)


def acquirable_gate_summary(options: Iterable[PathOption]) -> list[dict]:
    """Routes unlocked by something anyone can open — a card, a new account.

    §4.1 already treats these as annotations rather than filters, so they never
    move a row. But an annotation buried on row nine is not an answer; "this
    trip opens up if you start an Iberia Plus account" is the useful output and
    it has to be reported at the top level to be seen.
    """
    buckets: dict[tuple[str, str], dict] = {}
    for opt in options:
        for gate in opt.gates:
            if gate.kind is GateKind.HARD:
                continue  # a bank-tier requirement is not "easy to open"
            key = (gate.kind.value, gate.describe())
            entry = buckets.setdefault(
                key,
                {
                    "kind": gate.kind.value,
                    "requirement": gate.describe(),
                    "program": gate.program_id,
                    "card_ids": list(gate.card_ids),
                    "annual_fee_usd": gate.annual_fee_usd,
                    "approval_days": gate.approval_days,
                    "min_days": gate.min_days,
                    "routes": 0,
                },
            )
            entry["routes"] += 1
    return sorted(buckets.values(), key=lambda e: -e["routes"])


def rank_options(
    options: Iterable[PathOption],
    *,
    as_of: Optional[date] = None,
    balance_by_currency: Optional[dict[str, int]] = None,
    high_cash_usd: float = HIGH_FUEL_USD,
    limit: int = MAX_RESULTS,
    drop_unaffordable: bool = True,
    reach_multiple: float = REACH_MULTIPLE,
) -> tuple[list[PathOption], int]:
    """Affordability band → collapse dominated → §6.1 sort → top `limit`.

    Returns `(ranked, out_of_reach_count)`. The count is the whole point: a
    dropped row that nobody counts is indistinguishable from a row that never
    existed, which is how "no bookable option" got printed for a route where
    every candidate was priced correctly and merely cost more than the balance.

    Three bands, not two:
      affordable        source_points <= balance
      reach             balance < source_points <= balance * reach_multiple
                        — shown, sorted below affordable rows, shortfall named
      out of reach      beyond that — dropped, but returned as a count

    Affordability still orders before §6.1's cabin rule, so a first-class seat
    the user cannot pay for can never outrank a business seat they can.
    """
    opts = list(options)
    out_of_reach = 0

    if balance_by_currency is not None:
        priced: list[PathOption] = []
        for o in opts:
            balance = balance_by_currency.get(o.currency or "", 0)
            affordable = o.source_points <= balance
            shortfall = 0 if affordable else o.source_points - balance
            if not affordable and drop_unaffordable:
                if balance <= 0 or o.source_points > balance * reach_multiple:
                    out_of_reach += 1
                    continue
            priced.append(
                replace(o, affordable=affordable, shortfall_points=shortfall)
            )
        opts = priced

    opts = collapse_dominated(opts)
    # Affordable rows first, then reach rows by how short they fall. Without
    # this an unaffordable row could sort above an affordable one on points.
    opts.sort(
        key=lambda o: (
            0 if o.affordable else 1,
            o.shortfall_points,
            sort_key(o, high_cash_usd=high_cash_usd),
        )
    )
    opts = opts[:limit]
    return (
        [replace(o, reason=o.reason or explain(o, as_of=as_of)) for o in opts],
        out_of_reach,
    )


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
    "REACH_MULTIPLE",
    "acquirable_gate_summary",
    "collapse_dominated",
    "dominates",
    "explain",
    "partition_gated",
    "rank_options",
    "sort_key",
]
