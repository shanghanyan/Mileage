"""LLaVA context prompts by page category."""

from urllib.parse import urlparse


def page_category(url: str) -> str:
    lower = url.lower()
    if any(x in lower for x in ("award", "mile", "redeem", "chart")):
        return "award"
    if any(x in lower for x in ("credit-card", "creditcard", "sapphire", "thankyou", "transfer")):
        return "transfer"
    if any(x in lower for x in ("lounge", "club", "prioritypass")):
        return "lounge"
    if "google.com/travel" in lower or "kayak.com" in lower:
        return "cash_price"
    if any(x in lower for x in ("valuation", "pointsguy", "onemileatatime")):
        return "valuation"
    return "general"


PROMPTS = {
    "award": """You are extracting structured travel data from a screenshot of an airline website.
Look for: award flight prices in miles, cash prices in USD, origin/destination airports,
cabin class, partner airlines, transfer fees, and fuel surcharges.
Return ONLY a JSON object with these keys:
origin, destination, cabin, miles_cost, cash_cost_usd, taxes_fees_usd,
partner_airline, is_partner_flight, notes.
Use null for any field you cannot find.""",
    "transfer": """You are extracting credit card transfer partner data from a screenshot.
Look for: source currency name, destination loyalty programs, transfer ratios (e.g. 1:1),
active transfer bonuses, minimum transfer amounts, and transfer times.
Return ONLY a JSON object with keys:
source_currency, destination_program, transfer_ratio, transfer_bonus_active,
transfer_bonus_pct, transfer_bonus_expiry, min_transfer_units, transfer_time_hours.
Use null for missing fields.""",
    "lounge": """You are extracting airport lounge data from a screenshot.
Look for: airport codes, terminal, lounge name, operator, network, amenities
(showers, food, bar, wifi), hours, and access methods (cards or ticket class).
Return ONLY a JSON object with keys:
airport_iata, terminal, lounge_name, operated_by, network, access_via_cards,
access_via_ticket, has_showers, has_hot_food, has_bar, has_wifi, hours.
Use null for missing fields.""",
    "cash_price": """You are extracting cash flight prices from a travel aggregator screenshot.
Look for: origin/destination airports, cabin, price in USD, airline, stops, dates.
Return ONLY a JSON object with keys:
origin, destination, cabin, price_usd, airline, stops, departure_date, arrival_date.
Use null for missing fields.""",
    "valuation": """You are extracting points/miles valuation data from a blog screenshot.
Look for: loyalty program name, cabin class, cents per point/mile, publication date.
Return ONLY a JSON object with keys:
program, cabin, cents_per_point, valuation_date, source_name.
Use null for missing fields.""",
    "general": """You are extracting travel loyalty program data from a website screenshot.
Look for: program rules, partner airlines, credit card benefits, award pricing, lounges.
Return ONLY a JSON object with keys:
program, rule_topic, rule_summary, partner_airline, card_name, annual_fee_usd,
signup_bonus_points, miles_cost, origin, destination, cabin, notes.
Use null for missing fields.""",
}


def prompt_for_url(url: str) -> str:
    return PROMPTS.get(page_category(url), PROMPTS["general"])


def program_for_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    mapping = {
        "united.com": "united",
        "delta.com": "delta",
        "aa.com": "american",
        "ana.co.jp": "ana",
        "miles-and-more.com": "lufthansa",
        "aircanada.com": "aircanada",
        "singaporeair.com": "singapore",
        "turkishairlines.com": "turkish",
        "swiss.com": "swiss",
        "google.com": "google_flights",
        "kayak.com": "kayak",
        "thepointsguy.com": "thepointsguy",
        "onemileatatime.com": "onemileatatime",
    }
    for domain, program in mapping.items():
        if domain in host:
            return program
    return "unknown"
