"""Fast unit tests for contracts, chart detection, validation, and redirects."""

from __future__ import annotations

import pytest

from scrapers.bot_detection import detect_bot_block
from scrapers.chart_detection import detect_chart
from scrapers.chrome_scraper import VPS_CHROME_ARGS, _chrome_options
from scrapers.contracts import SITE_CONTRACTS, get_contract, resolve_url
from scrapers.fingerprint import DEFAULT_PROFILE, direct_proxy_type, load_vm_profile
from scrapers.inspect import inspect_keyword_hits, load_keywords
from scrapers.session import HostProfileError, require_remote_scrape
from scrapers.test_results import safe_name, start_run, write_result, write_summary
from scrapers.tor_scraper import DEFAULT_TOR_SOCKS, tor_socks
from scrapers.validation import (
    OUTCOME_BOT_BLOCKED,
    OUTCOME_CHART_NOT_FOUND,
    OUTCOME_EMPTY_CONTENT,
    OUTCOME_SUCCESS,
    OUTCOME_TIMEOUT,
    validate_scrape,
)

pytestmark = pytest.mark.unit

CHART_HTML = """
<html><head><title>Venture miles transfer partnerships</title></head>
<body>
<main>
<h1>Venture miles transfer partnerships</h1>
<p>Transfer your miles to airline and hotel partners at published conversion ratios.</p>
<table>
  <thead><tr><th>Partner</th><th>Miles</th><th>Points</th><th>Ratio</th></tr></thead>
  <tbody>
    <tr><td>Airline A</td><td>1000</td><td>1000</td><td>1:1</td></tr>
    <tr><td>Hotel B</td><td>2000</td><td>1000</td><td>2:1 transfer conversion</td></tr>
    <tr><td>Airline C</td><td>1500</td><td>1000</td><td>1.5:1 award</td></tr>
  </tbody>
</table>
</main>
</body></html>
"""


def test_three_site_contracts_present() -> None:
    assert set(SITE_CONTRACTS) == {
        "capitalone_venture_partners",
        "cathay_mega_miles_2026",
        "jal_partner_point",
    }
    for key, contract in SITE_CONTRACTS.items():
        assert contract.url.startswith("https://")
        assert contract.wait_selectors
        assert contract.chart_keywords
        assert contract.min_page_text_length > 0
        assert get_contract(key) is contract
    assert SITE_CONTRACTS["cathay_mega_miles_2026"].extra_urls


def test_redirect_aliases_resolve() -> None:
    assert "capitalone.com" in resolve_url("capitalone_venture_partners")
    assert "capitalone.com" in resolve_url("scrape://capitalone/venture-partners")
    assert "cathaypacific.com" in resolve_url("scrape://cathay/mega-miles-2026")
    assert "jal.co.jp" in resolve_url("scrape://jal/partner-point")


def test_chart_detection_meets_threshold() -> None:
    contract = get_contract("capitalone_venture_partners")
    chart = detect_chart(CHART_HTML, contract.chart_keywords)
    assert chart.found
    assert chart.confidence >= 0.45
    assert "Partner" in chart.headers
    assert len(chart.rows) == 3
    assert chart.table_index == 0
    assert "table" in chart.lxml_path


LIST_CHART_HTML = """
<html><body>
<ul>
  <li>1:1 ratio: 1,000 Capital One miles convert to 1,000 miles or points.</li>
  <li>2:1 ratio: 1,000 Capital One miles convert to 500 miles or points.</li>
  <li>5:3 ratio: 1,000 Capital One miles convert to 600 miles or points.</li>
</ul>
</body></html>
"""

DL_CHART_HTML = """
<html><body>
<dl><dt>Barclays</dt><dd>11,500 Arrival Premier miles = 5,000 Miles</dd><dd>22,500 Arrival Plus miles = 10,000 Miles</dd></dl>
<dl><dt>HSBC Taiwan</dt><dd>1 HSBC Traveller's Point = 1 Mile</dd></dl>
<dl><dt>Ping An WanLiTong</dt><dd>68 WanLiTong points = 1 Mile</dd></dl>
<dl><dt>CIMB</dt><dd>20 CIMB Bonus Points = 1 Mile</dd></dl>
</body></html>
"""


def test_chart_detection_accepts_ratio_lists() -> None:
    contract = get_contract("capitalone_venture_partners")
    chart = detect_chart(LIST_CHART_HTML, contract.chart_keywords)
    assert chart.found
    assert chart.confidence >= 0.45
    assert chart.source in {"list", "text"}
    assert len(chart.rows) >= 2
    assert any("1:1" in " ".join(row) for row in chart.rows)


def test_chart_detection_accepts_definition_lists() -> None:
    contract = get_contract("jal_partner_point")
    chart = detect_chart(DL_CHART_HTML, contract.chart_keywords)
    assert chart.found
    assert chart.confidence >= 0.45
    assert chart.source in {"dl", "dl_group"}
    assert any("Barclays" in " ".join(row) for row in chart.rows)


def test_validation_timeout_continues_when_page_loaded() -> None:
    contract = get_contract("capitalone_venture_partners")
    text = "Venture miles transfer partner Capital One award conversion ratio table " * 20
    result = validate_scrape(
        contract,
        html=CHART_HTML,
        visible_text=text,
        title="Venture miles",
        timed_out=True,
    )
    assert result.outcome == OUTCOME_SUCCESS
    assert result.deliverable_met is True


def test_validation_timeout_when_page_empty() -> None:
    contract = get_contract("capitalone_venture_partners")
    result = validate_scrape(
        contract,
        html="<html></html>",
        visible_text="",
        title="",
        timed_out=True,
    )
    assert result.outcome == OUTCOME_TIMEOUT
    assert result.deliverable_met is False


def test_validation_success_sets_deliverable() -> None:
    contract = get_contract("capitalone_venture_partners")
    text = "Venture miles transfer partner Capital One award conversion ratio table"
    result = validate_scrape(contract, html=CHART_HTML, visible_text=text * 20, title="Venture miles")
    assert result.outcome == OUTCOME_SUCCESS
    assert result.deliverable_met is True


def test_validation_empty_content() -> None:
    contract = get_contract("jal_partner_point")
    result = validate_scrape(contract, html="<html></html>", visible_text="short", title="JAL")
    assert result.outcome == OUTCOME_EMPTY_CONTENT
    assert result.deliverable_met is False


def test_validation_bot_block_from_title() -> None:
    contract = get_contract("cathay_mega_miles_2026")
    result = validate_scrape(
        contract,
        html="<html><body>Pardon Our Interruption</body></html>",
        visible_text="Pardon Our Interruption " * 80,
        title="Access Denied",
    )
    assert result.outcome == OUTCOME_BOT_BLOCKED


def test_validation_chart_not_found() -> None:
    contract = get_contract("capitalone_venture_partners")
    html = "<html><body><main>" + ("venture miles partner capital one content " * 80) + "</main></body></html>"
    text = "venture miles partner capital one content " * 80
    result = validate_scrape(contract, html=html, visible_text=text, title="Venture")
    assert result.outcome == OUTCOME_CHART_NOT_FOUND
    assert result.deliverable_met is False


def test_akamai_challenge_is_blocked() -> None:
    html = "<html>geo.captcha-delivery.com ddchallenge</html>"
    bot = detect_bot_block(html, title="Just a moment...", visible_text="verify you are human")
    assert bot.blocked is True


def test_inspect_keywords_load_and_hit() -> None:
    groups = load_keywords()
    assert "miles" in groups and "chart" in groups
    hits = inspect_keyword_hits(CHART_HTML)
    assert any(h["keyword"] in {"mile", "miles", "transfer", "conversion", "ratio"} for h in hits)


def test_require_remote_scrape_blocks_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMOTE_SCRAPE", raising=False)
    with pytest.raises(HostProfileError):
        require_remote_scrape()
    monkeypatch.setenv("REMOTE_SCRAPE", "1")
    require_remote_scrape()


def test_vps_defaults() -> None:
    assert DEFAULT_PROFILE["hypervisor"] == "kvm"
    assert "utm" not in DEFAULT_PROFILE["machine_id"]
    profile = load_vm_profile()
    assert profile.os == "Linux"
    assert profile.screen_width == 1920
    assert DEFAULT_TOR_SOCKS.endswith(":9050")


def test_direct_proxy_type_defaults_to_vps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCRAPE_PROXY_TYPE", raising=False)
    assert direct_proxy_type() == "vps"
    monkeypatch.setenv("SCRAPE_PROXY_TYPE", "vpn")
    assert direct_proxy_type() == "vpn"


def test_chrome_vps_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHROME_NO_SANDBOX", raising=False)
    options = _chrome_options("/tmp/chrome-profile-test", load_vm_profile())
    args = options.arguments
    for flag in VPS_CHROME_ARGS:
        assert flag in args
    assert any(a.startswith("--window-size=1920,1080") for a in args)
    monkeypatch.setenv("CHROME_NO_SANDBOX", "0")
    options = _chrome_options("/tmp/chrome-profile-test", load_vm_profile())
    assert "--no-sandbox" not in options.arguments


def test_test_results_write_json(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_RESULTS_DIR", str(tmp_path))
    run_dir = start_run("unit")
    path = write_result(run_dir, "sample::case", {"outcome": "passed"})
    summary = write_summary(run_dir, {"total": 1})
    assert path.is_file()
    assert summary.name == "summary.json"
    assert safe_name("sample::case") == "sample_case"
    assert path.read_text(encoding="utf-8").strip().startswith("{")
    run_json = run_dir / "run.json"
    assert run_json.is_file()
    assert "running" in run_json.read_text(encoding="utf-8")


def test_tor_socks_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOR_SOCKS", raising=False)
    assert tor_socks() == ("127.0.0.1", 9050)
    monkeypatch.setenv("TOR_SOCKS", "127.0.0.1:9150")
    assert tor_socks() == ("127.0.0.1", 9150)
