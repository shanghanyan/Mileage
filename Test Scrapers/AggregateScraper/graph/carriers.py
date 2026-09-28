"""Operating carrier assignment for redemption paths."""

from __future__ import annotations

CARRIER_NAMES = {
    "UA": "United",
    "NH": "ANA",
    "AC": "Air Canada",
    "SQ": "Singapore Airlines",
    "BR": "EVA Air",
    "AY": "Finnair",
    "JL": "JAL",
    "CX": "Cathay Pacific",
    "QF": "Qantas",
    "AF": "Air France",
    "DL": "Delta",
    "EK": "Emirates",
    "EY": "Etihad",
    "VS": "Virgin Atlantic",
}

PARTNER_CARRIER_MAP: dict[tuple[str, str, str], list[tuple[str, str]]] = {
    ("lifemiles", "North America", "North Asia"): [("UA", "United"), ("NH", "ANA")],
    ("aeroplan", "North America", "North Asia"): [("UA", "United"), ("NH", "ANA"), ("AC", "Air Canada")],
    ("turkish_miles", "North America", "North Asia"): [("UA", "United"), ("NH", "ANA")],
    ("ana_mileage", "North America", "North Asia"): [("NH", "ANA")],
    ("krisflyer", "North America", "North Asia"): [("SQ", "Singapore Airlines")],
    ("eva_air", "North America", "North Asia"): [("BR", "EVA Air")],
    ("finnair", "North America", "North Asia"): [("AY", "Finnair"), ("NH", "ANA")],
    ("avios", "North America", "North Asia"): [("JL", "JAL")],
    ("asia_miles", "North America", "North Asia"): [("JL", "JAL"), ("CX", "Cathay Pacific")],
    ("qatar", "North America", "North Asia"): [("JL", "JAL")],
    ("jal", "North America", "North Asia"): [("JL", "JAL")],
    ("qantas", "North America", "North Asia"): [("JL", "JAL"), ("QF", "Qantas")],
    ("flying_blue", "North America", "North Asia"): [("AF", "Air France"), ("DL", "Delta")],
    ("emirates", "North America", "North Asia"): [("EK", "Emirates")],
    ("etihad", "North America", "North Asia"): [("EY", "Etihad")],
    ("jetblue", "North America", "North Asia"): [],
    ("virgin_red", "North America", "North Asia"): [("VS", "Virgin Atlantic")],
}

TRANSATLANTIC_AIRPORTS = {
    "JFK", "LAX", "ORD", "SFO", "IAD", "MIA", "BOS", "SEA", "EWR", "ATL",
}
NORTH_ASIA_AIRPORTS = {"NRT", "HND", "ICN", "PEK", "PVG", "HKG", "TPE", "SIN"}


def airport_region(airport: str) -> str:
    code = airport.upper()
    if code in TRANSATLANTIC_AIRPORTS:
        return "North America"
    if code in NORTH_ASIA_AIRPORTS:
        return "North Asia"
    return "Other"


def get_carrier_label(
    program_id: str,
    origin_region: str,
    dest_region: str,
) -> str:
    key = (program_id, origin_region, dest_region)
    carriers = PARTNER_CARRIER_MAP.get(key, [])
    if not carriers:
        return "partner airline"
    if len(carriers) == 1:
        return carriers[0][1]
    return f"{carriers[0][1]} (or {carriers[1][1]})"


def get_carrier_codes(
    program_id: str,
    origin_region: str,
    dest_region: str,
) -> str:
    key = (program_id, origin_region, dest_region)
    carriers = PARTNER_CARRIER_MAP.get(key, [])
    if not carriers:
        return "—"
    return " / ".join(f"{code} ({name})" for code, name in carriers[:2])


def build_path_label(
    program_id: str,
    program_name: str,
    origin: str,
    dest: str,
    *,
    routing_type: str | None = None,
    connection_hub: str | None = None,
) -> str:
    origin_region = airport_region(origin)
    dest_region = airport_region(dest)
    carrier = get_carrier_label(program_id, origin_region, dest_region)

    if program_id == "krisflyer" or (routing_type == "connecting" and connection_hub):
        hub = connection_hub or "SIN"
        return f"C1 → {program_name} → {carrier} (via {hub})"

    display_name = program_name if program_name else program_id
    return f"C1 → {display_name} → {carrier}"
