import math
import networkx as nx

from graph.edges import TransferEdge, AwardEdge, PortalEdge
from graph.zones import zone_slug


def build_graph(
    transfer_edges: list[TransferEdge],
    award_edges: list[AwardEdge],
    portal_edge: PortalEdge,
) -> nx.DiGraph:
    g = nx.DiGraph()

    g.add_edge(
        "capital_one",
        "usd_portal",
        cpp=portal_edge.cpp,
        edge_type="portal",
        confidence="high",
        source=portal_edge.source,
        flags="",
    )

    for te in transfer_edges:
        if te.confidence not in ("high", "medium", "low"):
            continue
        g.add_edge(
            te.from_currency,
            te.to_currency,
            ratio=te.ratio,
            effective_ratio=te.ratio,
            edge_type="transfer",
            confidence=te.confidence,
            source=te.source,
            flags=",".join(te.flags),
        )

    for ae in award_edges:
        if ae.confidence == "unverified":
            continue
        origin_slug = zone_slug(ae.origin_zone)
        dest_slug = zone_slug(ae.destination_zone)
        program = ae.from_currency
        for cabin, miles in [
            ("economy", ae.economy_miles),
            ("business", ae.business_miles),
            ("first", ae.first_miles),
        ]:
            if miles is None:
                continue
            node = f"{program}_{cabin}_{origin_slug}_{dest_slug}"
            g.add_edge(
                program,
                node,
                miles=miles,
                miles_range_low=ae.miles_range_low,
                miles_range_high=ae.miles_range_high,
                edge_type="award",
                confidence=ae.confidence,
                source=ae.source,
                source_count=getattr(ae, "source_count", 1),
                source_updated_at=ae.scraped_at.isoformat() if ae.scraped_at else None,
                source_trust=getattr(ae, "source_trust", 0.40),
                flags=",".join(ae.flags),
            )

    return g
