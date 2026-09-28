"""Ingest artifact JSON into DRF without running Selenium."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def django_ready() -> None:
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ["DJANGO_TEST_INMEMORY"] = "1"
    import django

    django.setup()
    from django.core.management import call_command

    call_command("migrate", verbosity=0)


def test_ingest_and_deliverables(django_ready, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from django.test import Client

    from scrapes.models import ScrapeExport

    artifact = {
        "alias": "scrape://capitalone/venture-partners",
        "key": "capitalone_venture_partners",
        "tier": 2,
        "outcome": "success",
        "deliverable_met": True,
        "chart_confidence": 0.72,
        "chart_headers": ["Partner", "Ratio"],
        "chart_rows": [["Airline A", "1:1"]],
        "lxml_path": "/html/body/table[1]",
        "selenium_xpath": "(//table)[1]",
        "selenium_css": "table:nth-of-type(1)",
        "egress_ip": "203.0.113.10",
        "proxy_type": "home_nat",
        "html_path": "artifacts/chrome/sample.html",
        "screenshot_path": "artifacts/chrome/sample.png",
        "inspect_hits": [{"group": "miles", "keyword": "mile", "snippet": "miles"}],
        "scraped_at": "2026-09-08T04:00:00+00:00",
    }
    (tmp_path / "sample.json").write_text(json.dumps(artifact), encoding="utf-8")

    from django.conf import settings

    monkeypatch.setattr(settings, "SCRAPE_ARTIFACTS_DIR", tmp_path)

    client = Client()
    ingest = client.post("/api/ingest/", data=json.dumps({}), content_type="application/json")
    assert ingest.status_code == 200
    assert ingest.json()["ingested"] == 1
    assert ScrapeExport.objects.filter(deliverable_met=True).count() == 1

    deliverables = client.get("/api/exports/deliverables/")
    assert deliverables.status_code == 200
    rows = deliverables.json()
    assert rows[0]["chart_headers"] == ["Partner", "Ratio"]
    assert rows[0]["deliverable_met"] is True

    detail = client.get("/api/exports/scrape://capitalone/venture-partners/")
    assert detail.status_code == 200
    assert detail.json()[0]["selenium_xpath"] == "(//table)[1]"


def test_ingest_artifacts_command(django_ready, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from django.conf import settings
    from django.core.management import call_command

    from scrapes.models import ScrapeExport

    payload = {
        "alias": "scrape://jal/partner-point",
        "key": "jal_partner_point",
        "tier": 1,
        "outcome": "chart_not_found",
        "deliverable_met": False,
        "chart_confidence": 0.1,
        "chart_headers": [],
        "chart_rows": [],
        "scraped_at": "2026-09-11T00:00:00+00:00",
    }
    (tmp_path / "jal.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(settings, "SCRAPE_ARTIFACTS_DIR", tmp_path)
    call_command("ingest_artifacts")
    assert ScrapeExport.objects.filter(key="jal_partner_point").exists()
