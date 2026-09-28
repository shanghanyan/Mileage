from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime
from enum import Enum


class Currency(str, Enum):
    """Every node in the conversion graph. USD is the terminal sink."""
    C1_MILES      = "capital_one_miles"
    C1_CASHBACK   = "capital_one_cashback_usd"
    LIFEMILES     = "avianca_lifemiles"
    TURKISH_MILES = "turkish_miles_smiles"
    KRISFLYER     = "singapore_krisflyer"
    AEROPLAN      = "air_canada_aeroplan"
    USD           = "us_dollars"


class ScraperMethod(str, Enum):
    PLAYWRIGHT     = "playwright"
    PLAYWRIGHT_BS4 = "playwright_bs4"
    HTTPX_BS4      = "httpx_bs4"
    VISION         = "vision"


class ScraperStatus(str, Enum):
    SUCCESS  = "SUCCESS"
    CACHED   = "CACHED"
    RETRYING = "RETRYING"
    FAILED   = "FAILED"
    SKIPPED  = "SKIPPED"


class ScraperResult(BaseModel):
    scraper_name: str
    source_url:   str
    method:       ScraperMethod
    status:       ScraperStatus
    data:         dict
    error:        Optional[str] = None
    duration_ms:  int
    scraped_at:   datetime = Field(default_factory=datetime.utcnow)


class TransferEdge(BaseModel):
    """
    A single directed edge in the weighted conversion graph.

    `ratio` is the exchange rate: how many units of `to_currency` you get
    per 1 unit of `from_currency`.

    `edge_cpp` is the contribution of this edge to the final CPP calculation:
      - For transfer edges  (e.g. C1_MILES → LIFEMILES):   edge_cpp = ratio  (pass-through)
      - For redemption edges (e.g. LIFEMILES → USD):        edge_cpp = (cash_price_cents / miles_needed)
      - For direct cash edges (e.g. C1_MILES → USD):        edge_cpp = cpp directly (0.5, 0.8, 1.0)

    The optimizer multiplies all edge_cpp values along a path to get final CPP
    in cents per original C1 mile.
    """
    from_currency:  Currency
    to_currency:    Currency
    ratio:          float
    edge_cpp:       float
    label:          str
    source_name:    str
    source_url:     str
    scraped_at:     datetime = Field(default_factory=datetime.utcnow)
    is_one_way:     bool = False
    stale:          bool = False
    suspicious:     bool = False
    notes:          Optional[str] = None

    @field_validator("edge_cpp")
    @classmethod
    def cpp_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"edge_cpp must be positive, got {v}")
        return v


class RedemptionPath(BaseModel):
    path_label:   str
    hops:         list[TransferEdge]
    total_cpp:    float
    source_start: Currency
    is_one_way:   bool = False
    has_stale:    bool = False
    has_suspicious: bool = False
    recommended:  bool = False


class UserPortfolio(BaseModel):
    c1_miles:        float = 0.0
    c1_cashback_usd: float = 0.0
    lifemiles:       float = 0.0
    turkish_miles:   float = 0.0
    krisflyer_miles: float = 0.0
    aeroplan_miles:  float = 0.0


class HoldingValuation(BaseModel):
    currency:     Currency
    amount:       float
    best_path:    RedemptionPath
    value_usd:    float


class OptimizationResult(BaseModel):
    portfolio:            UserPortfolio
    holding_valuations:   list[HoldingValuation]
    all_paths:            list[RedemptionPath]
    total_value_usd:      float
    generated_at:         datetime = Field(default_factory=datetime.utcnow)
