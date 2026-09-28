import networkx as nx
from datetime import datetime
from optimizer.models import TransferEdge, Currency


def get_static_edges() -> list[TransferEdge]:
    now = datetime.utcnow()
    static_url = "https://www.capitalone.com/credit-cards/miles/"
    return [
        TransferEdge(
            from_currency=Currency.C1_MILES, to_currency=Currency.USD,
            ratio=1.0, edge_cpp=0.5,
            label="Statement credit",
            source_name="static_config", source_url=static_url,
            scraped_at=now
        ),
        TransferEdge(
            from_currency=Currency.C1_MILES, to_currency=Currency.USD,
            ratio=1.0, edge_cpp=0.8,
            label="Gift cards / Amazon",
            source_name="static_config", source_url=static_url,
            scraped_at=now
        ),
        TransferEdge(
            from_currency=Currency.C1_MILES, to_currency=Currency.USD,
            ratio=1.0, edge_cpp=1.0,
            label="Capital One Travel Portal",
            source_name="static_config", source_url=static_url,
            scraped_at=now
        ),
        TransferEdge(
            from_currency=Currency.C1_CASHBACK, to_currency=Currency.USD,
            ratio=1.0, edge_cpp=100.0,
            label="Cashback → statement credit (face value)",
            source_name="static_config", source_url=static_url,
            scraped_at=now,
            notes="C1_CASHBACK units are USD, so cpp=100 means 1 dollar = 100 cents"
        ),
        TransferEdge(
            from_currency=Currency.C1_CASHBACK, to_currency=Currency.C1_MILES,
            ratio=100.0, edge_cpp=100.0,
            label="Cashback → C1 Miles (one-way, irreversible)",
            source_name="static_config", source_url=static_url,
            scraped_at=now,
            is_one_way=True,
            notes="After conversion, miles cannot be converted back to cashback"
        ),
    ]


def build_graph(edges: list[TransferEdge]) -> nx.MultiDiGraph:
    G = nx.MultiDiGraph()
    for e in edges:
        G.add_edge(
            e.from_currency,
            e.to_currency,
            key=e.label,
            weight=-e.edge_cpp,
            edge_cpp=e.edge_cpp,
            ratio=e.ratio,
            label=e.label,
            is_one_way=e.is_one_way,
            stale=e.stale,
            suspicious=e.suspicious,
            source_name=e.source_name,
            source_url=e.source_url,
            scraped_at=e.scraped_at.isoformat(),
        )
    return G
