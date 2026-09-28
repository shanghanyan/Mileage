import re
from pathlib import Path

import yaml


def zone_slug(zone_name: str) -> str:
    slug = zone_name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


def load_zone_mapping(config_path: Path | None = None) -> dict[str, dict[str, str]]:
    path = config_path or Path(__file__).resolve().parent.parent / "config" / "zone_mapping.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def airport_to_zone(airport: str, program: str, mapping: dict) -> str | None:
    return mapping.get(program, {}).get(airport.upper())
