"""§8 compile step — validate Tables 1 + 3 and emit a hashed snapshot.

    build/graph-2026-08-13-a1f39c.json

Two jobs:

1. **Referential integrity + staleness as a build failure.** Every
   `transfers[].to` must resolve, every chart currency must exist, every carrier
   named in fuel_charges / partners / service must exist, and no record may be
   older than the staleness window. §7's table says these are CI failures, not
   comments — a silently wrong answer served to a user is strictly worse than a
   red build.

2. **A content hash that identifies the data a result was computed from.**
   Every result carries it, so you can reproduce exactly why the engine said
   what it said last Tuesday.

The hash also does operational work the architecture doesn't spell out but the
system badly needs: it is mixed into every provider cache key. Without that, a
change to a chart or to the meaning of a field keeps serving pre-change entries
out of Redis for the full 2-day TTL — which is exactly how a retired per-program
surcharge went on pricing routes for three code changes after it was deleted.
Keying the cache by data version makes a knowledge edit self-invalidating.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

# Files that constitute Tables 1 and 3. Table 2 (promotions) is deliberately
# excluded: it is scraper-owned and changes weekly, so folding it into the
# snapshot hash would invalidate every cache entry on every bonus refresh.
TABLE_FILES: tuple[str, ...] = (
    "ratios.yaml",
    "charts.yaml",
    "alliances.yaml",
    "airports.yaml",
    "carriers.yaml",
    "partners.yaml",
    "fuel_charges.yaml",
    "cards.yaml",
    "fares.yaml",
)

# §7: "`verifiedAt` older than 90 days -> CI build failure".
STALENESS_DAYS = 90


@dataclass
class SnapshotIssue:
    kind: str          # integrity | staleness | parse
    where: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.where}: {self.detail}"


@dataclass
class Snapshot:
    hash: str
    built_at: str
    files: dict[str, str]                       # filename -> sha256
    issues: list[SnapshotIssue] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def blocking(self) -> list[SnapshotIssue]:
        return [i for i in self.issues if i.kind != "staleness"]

    def name(self) -> str:
        return f"graph-{self.built_at[:10]}-{self.hash[:6]}"


def _read(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _iter_dates(node: Any, path: str = "") -> Iterable[tuple[str, date]]:
    """Every updated_at / verifiedAt / verified_at in the tree, with its path."""
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else str(k)
            if k in ("updated_at", "verifiedAt", "verified_at") and v is not None:
                try:
                    yield here, date.fromisoformat(str(v)[:10])
                except ValueError:
                    continue
            else:
                yield from _iter_dates(v, here)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_dates(v, f"{path}[{i}]")


def _hotel_programs(data: dict[str, Any]) -> set[str]:
    """Hotel currencies are valid transfer endpoints with no airline chart.

    They are legitimately programs — Marriott is a `from_program` on real
    transfer edges — they just never terminate a REDEEM edge, so demanding an
    award chart for them would flag correct data as broken.
    """
    out: set[str] = set()
    ratios = data.get("ratios.yaml") or {}
    for block in [ratios, *(ratios.get("currencies") or [])]:
        if isinstance(block, dict) and block.get("from_currency"):
            out.add(str(block["from_currency"]))
    for row in (data.get("alliances.yaml") or {}).get("program_transfers") or []:
        if isinstance(row, dict) and row.get("hotel"):
            out.add(str(row.get("from") or row.get("from_program") or ""))
    out |= {"marriott_bonvoy", "hilton", "hyatt", "ihg", "choice", "wyndham", "accor"}
    return {p for p in out if p}


def _alliance_of_program(data: dict[str, Any]) -> dict[str, dict[str, set[str]]]:
    """program -> {file: {alliance_id, ...}} across every file asserting membership.

    The value is a SET, not a string. A program listed in two rosters inside the
    same file is itself the error being hunted — the first version of this
    function stored a bare string, so the second roster silently overwrote the
    first and a program in both `oneworld` and `independent` looked consistent.
    """
    out: dict[str, dict[str, set[str]]] = {}

    def note(program: Any, alliance: Any, where: str) -> None:
        if not program or not alliance:
            return
        out.setdefault(str(program), {}).setdefault(where, set()).add(str(alliance))

    for aid, spec in ((data.get("alliances.yaml") or {}).get("alliances") or {}).items():
        if isinstance(spec, dict):
            for program in spec.get("programs") or []:
                note(program, aid, "alliances.yaml")

    for program, spec in ((data.get("partners.yaml") or {}).get("currencies") or {}).items():
        if isinstance(spec, dict):
            note(program, spec.get("alliance"), "partners.yaml")

    for _cid, spec in ((data.get("carriers.yaml") or {}).get("carriers") or {}).items():
        if isinstance(spec, dict):
            note(spec.get("program"), spec.get("alliance"), "carriers.yaml")

    for program, spec in ((data.get("charts.yaml") or {}).get("programs") or {}).items():
        if isinstance(spec, dict):
            note(program, spec.get("alliance"), "charts.yaml")

    return out


def _check_alliance_consistency(data: dict[str, Any]) -> list[SnapshotIssue]:
    """Alliance membership must agree across every file that claims it.

    Membership is not decorative: partners.yaml grants a currency booking
    rights on every carrier in its alliance, so one wrong word here invents
    itineraries. Listing Aer Lingus AerClub as `oneworld` gave it rights on all
    ~15 oneworld carriers and put "Avios -> AerClub on American Airlines" at the
    top of four routes in a twelve-day sweep — a redemption that cannot be
    booked, ranked #1, with no flag on it.

    This does not attempt to know the real-world rosters; asserting those is a
    human's job (see docs/KNOWN_WORLD.md). It enforces the weaker property a
    machine CAN check: the four files never disagree, so a correction applied
    to one of them can't leave the other three quietly wrong.
    """
    issues: list[SnapshotIssue] = []
    for program, by_file in sorted(_alliance_of_program(data).items()):
        for where, claimed in sorted(by_file.items()):
            if len(claimed) > 1:
                issues.append(
                    SnapshotIssue(
                        "integrity",
                        f"alliance membership for {program!r}",
                        f"{where} lists it in {len(claimed)} alliances at once: "
                        + ", ".join(sorted(claimed)),
                    )
                )
        across = {a for claimed in by_file.values() for a in claimed}
        if len(across) > 1:
            detail = ", ".join(
                f"{f} says {'/'.join(sorted(a))}" for f, a in sorted(by_file.items())
            )
            issues.append(
                SnapshotIssue(
                    "integrity",
                    f"alliance membership for {program!r}",
                    f"files disagree — {detail}",
                )
            )
    return issues


def _check_alliance_provenance(data: dict[str, Any]) -> list[SnapshotIssue]:
    """Every alliance roster must carry a source + verified_at.

    A roster with no provenance cannot be vetted, and an un-vettable claim is
    how a program sits in the wrong alliance for months. Membership changes are
    real and regular (SAS moved Star -> SkyTeam in 2024; Oman Air joined
    oneworld in 2025), so the roster needs a date attached like every other
    record in the Known World.
    """
    issues: list[SnapshotIssue] = []
    for aid, spec in ((data.get("alliances.yaml") or {}).get("alliances") or {}).items():
        if not isinstance(spec, dict):
            continue
        for key in ("source", "verified_at"):
            if not spec.get(key):
                issues.append(
                    SnapshotIssue(
                        "integrity",
                        f"alliances.yaml {aid}",
                        f"roster has no {key!r} — membership cannot be vetted",
                    )
                )
    return issues


def _check_carrier_ids_are_iata(data: dict[str, Any]) -> list[SnapshotIssue]:
    """Carrier keys must be 2-character IATA codes.

    `carriers.yaml` was keyed `JAL` for Japan Airlines while every other entry
    used IATA (`BA`, `AA`, `QF`). Internally consistent, so nothing caught it —
    but the id is what an award provider's `operating_carrier` field gets
    matched against, and live feeds emit `JL`. The mismatch would silently fail
    to resolve exactly the carrier the flagship HND-ITM demo depends on.
    """
    issues: list[SnapshotIssue] = []
    for cid in (data.get("carriers.yaml") or {}).get("carriers") or {}:
        code = str(cid)
        if len(code) != 2 or not code.isalnum() or not code.isupper():
            issues.append(
                SnapshotIssue(
                    "integrity",
                    "carriers.yaml",
                    f"carrier id {code!r} is not a 2-character uppercase IATA "
                    "code; live providers key on IATA and will not match it",
                )
            )
    return issues


def _check_integrity(data: dict[str, Any]) -> list[SnapshotIssue]:
    issues: list[SnapshotIssue] = []
    issues.extend(_check_alliance_consistency(data))
    issues.extend(_check_alliance_provenance(data))
    issues.extend(_check_carrier_ids_are_iata(data))

    charts = (data.get("charts.yaml") or {}).get("programs") or {}
    alliances_raw = (data.get("alliances.yaml") or {}).get("alliances") or {}
    alliance_programs: set[str] = set()
    for spec in alliances_raw.values():
        if isinstance(spec, dict):
            alliance_programs |= {str(p) for p in (spec.get("programs") or [])}

    carriers = (data.get("carriers.yaml") or {}).get("carriers") or {}
    carrier_ids = {str(c).upper() for c in carriers}
    carrier_programs = {
        str(spec.get("program"))
        for spec in carriers.values()
        if isinstance(spec, dict) and spec.get("program")
    }

    airports = set((data.get("airports.yaml") or {}).get("airports") or {})
    legacy_airports = set((data.get("charts.yaml") or {}).get("region_map") or {})
    known_airports = {a.upper() for a in airports | legacy_airports}

    # 1. Every program→program transfer resolves to a program we know.
    known_programs = (
        set(charts) | alliance_programs | carrier_programs | _hotel_programs(data)
    )
    for row in (data.get("alliances.yaml") or {}).get("program_transfers") or []:
        if not isinstance(row, dict):
            continue
        for side in ("from", "to"):
            prog = row.get(side) or row.get(f"{side}_program")
            if prog and str(prog) not in known_programs:
                issues.append(
                    SnapshotIssue(
                        "integrity",
                        f"alliances.yaml program_transfers[{side}]",
                        f"{prog!r} is not a known program",
                    )
                )

    # 2. Every transfer-ratio destination resolves.
    ratios = data.get("ratios.yaml") or {}
    blocks = [ratios, *(ratios.get("currencies") or [])]
    for block in blocks:
        if not isinstance(block, dict):
            continue
        src = block.get("from_currency", "?")
        for prog in (block.get("partners") or {}):
            if str(prog) not in known_programs:
                issues.append(
                    SnapshotIssue(
                        "integrity",
                        f"ratios.yaml {src}",
                        f"partner {prog!r} has no chart, alliance or carrier entry",
                    )
                )

    # 3. Every carrier named in fuel_charges / partners / carriers exists.
    for row in (data.get("fuel_charges.yaml") or {}).get("matrix") or []:
        if not isinstance(row, dict):
            continue
        carrier = str(row.get("carrier") or "*")
        if carrier != "*" and carrier.upper() not in carrier_ids:
            issues.append(
                SnapshotIssue(
                    "integrity",
                    "fuel_charges.yaml matrix",
                    f"carrier {carrier!r} is not in carriers.yaml",
                )
            )

    for currency, spec in (
        (data.get("partners.yaml") or {}).get("currencies") or {}
    ).items():
        if not isinstance(spec, dict):
            continue
        for key in ("extra", "excluded"):
            for carrier in spec.get(key) or []:
                if str(carrier).upper() not in carrier_ids:
                    issues.append(
                        SnapshotIssue(
                            "integrity",
                            f"partners.yaml {currency}.{key}",
                            f"carrier {carrier!r} is not in carriers.yaml",
                        )
                    )

    # 4. Every hub/route airport in the service map is a known airport, so a
    #    typo'd hub can't silently remove a carrier from every search.
    for cid, spec in carriers.items():
        if not isinstance(spec, dict):
            continue
        for hub in spec.get("hubs") or []:
            if str(hub).upper() not in known_airports:
                issues.append(
                    SnapshotIssue(
                        "integrity",
                        f"carriers.yaml {cid}.hubs",
                        f"{hub!r} is not in airports.yaml",
                    )
                )
        for row in spec.get("routes") or []:
            if not isinstance(row, dict):
                continue
            for side in ("from", "to"):
                ap = row.get(side)
                if ap and str(ap).upper() not in known_airports:
                    issues.append(
                        SnapshotIssue(
                            "integrity",
                            f"carriers.yaml {cid}.routes",
                            f"{ap!r} is not in airports.yaml",
                        )
                    )

    # 5. Every chart program that names an alliance names a real one.
    for program, spec in charts.items():
        if not isinstance(spec, dict):
            continue
        alliance = spec.get("alliance")
        if alliance and str(alliance) not in alliances_raw:
            issues.append(
                SnapshotIssue(
                    "integrity",
                    f"charts.yaml {program}",
                    f"alliance {alliance!r} is not in alliances.yaml",
                )
            )

    return issues


def _check_staleness(
    data: dict[str, Any], *, as_of: date, max_age_days: int
) -> list[SnapshotIssue]:
    issues: list[SnapshotIssue] = []
    for filename, tree in data.items():
        for where, stamp in _iter_dates(tree):
            age = (as_of - stamp).days
            if age > max_age_days:
                issues.append(
                    SnapshotIssue(
                        "staleness",
                        f"{filename} {where}",
                        f"verified {stamp.isoformat()} — {age} days old "
                        f"(limit {max_age_days})",
                    )
                )
    return issues


def build_snapshot(
    knowledge_dir: Path,
    *,
    as_of: Optional[date] = None,
    max_age_days: int = STALENESS_DAYS,
) -> Snapshot:
    """Parse, validate, and hash Tables 1 + 3."""
    as_of = as_of or date.today()
    data: dict[str, Any] = {}
    digests: dict[str, str] = {}

    issues: list[SnapshotIssue] = []
    for filename in TABLE_FILES:
        path = knowledge_dir / filename
        if not path.exists():
            continue
        raw = path.read_bytes()
        digests[filename] = hashlib.sha256(raw).hexdigest()
        try:
            data[filename] = _read(path)
        except yaml.YAMLError as exc:
            issues.append(SnapshotIssue("parse", filename, str(exc)))

    issues += _check_integrity(data)
    issues += _check_staleness(data, as_of=as_of, max_age_days=max_age_days)

    combined = hashlib.sha256(
        json.dumps(digests, sort_keys=True).encode("utf-8")
    ).hexdigest()

    charts = (data.get("charts.yaml") or {}).get("programs") or {}
    carriers = (data.get("carriers.yaml") or {}).get("carriers") or {}
    return Snapshot(
        hash=combined,
        built_at=datetime.now(timezone.utc).isoformat(),
        files=digests,
        issues=issues,
        stats={
            "programs": len(charts),
            "carriers": len(carriers),
            "airports": len((data.get("airports.yaml") or {}).get("airports") or {}),
            "bands": sum(
                len(s.get("bands") or []) for s in charts.values() if isinstance(s, dict)
            ),
            "fuel_matrix_rows": len(
                (data.get("fuel_charges.yaml") or {}).get("matrix") or []
            ),
        },
    )


@lru_cache(maxsize=8)
def _cached_hash(knowledge_dir: str) -> str:
    """Content hash only — cheap, and never raises on a validation problem.

    Deliberately does NOT validate: this is on the hot path for cache keys, and
    a data problem should fail the build (via `mileage compile`), not every
    query at runtime.
    """
    root = Path(knowledge_dir)
    digests = {}
    for filename in TABLE_FILES:
        path = root / filename
        if path.exists():
            digests[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(
        json.dumps(digests, sort_keys=True).encode("utf-8")
    ).hexdigest()


def snapshot_hash(knowledge_dir: Optional[Path] = None) -> str:
    from .config import Config

    root = knowledge_dir or Config.from_env().knowledge_dir
    return _cached_hash(str(root))


def snapshot_version(knowledge_dir: Optional[Path] = None) -> str:
    """Short form used in cache keys and result provenance."""
    return snapshot_hash(knowledge_dir)[:12]


def write_snapshot(snapshot: Snapshot, build_dir: Path) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    out = build_dir / f"{snapshot.name()}.json"
    out.write_text(
        json.dumps(
            {
                "hash": snapshot.hash,
                "built_at": snapshot.built_at,
                "files": snapshot.files,
                "stats": snapshot.stats,
                "issues": [
                    {"kind": i.kind, "where": i.where, "detail": i.detail}
                    for i in snapshot.issues
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


__all__ = [
    "STALENESS_DAYS",
    "Snapshot",
    "SnapshotIssue",
    "TABLE_FILES",
    "build_snapshot",
    "snapshot_hash",
    "snapshot_version",
    "write_snapshot",
]
