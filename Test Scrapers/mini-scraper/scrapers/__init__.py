from pathlib import Path
from functools import lru_cache

import yaml


@lru_cache
def _config() -> dict:
    path = Path(__file__).resolve().parent.parent / "config" / "scraper_config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def scraper_config(key: str) -> dict:
    return _config()["scrapers"][key]
