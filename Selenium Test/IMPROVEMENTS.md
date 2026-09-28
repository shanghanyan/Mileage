# Improvement note

Captured 2026-09-27 after the VPS run that got Capital One (HTTP) and JAL (Chrome) and missed Cathay. Not implemented.

Drop the **0.45 confidence gate**. A scrape succeeds when conversion rows are extracted (partner + rate, or ratio list). It fails when there are no rows. Do not score keyword/numeric/size and then reject a real chart at 0.33.

## Why the last run still needs this

- **JAL HTTP** died on a datacenter IP (`Access Denied`). Chrome on the same VPS IP worked.
- **Cathay** landing page had promo copy, not rates. Chrome followed one extra URL and that hop failed (`ERR_HTTP2_PROTOCOL_ERROR`). One hop is not enough, and a failed hop is not “no chart.”
- **Capital One** HTTP worked because the ratios were already in the HTML. That path does not help JS/Akamai pages.

## Work items

### Multi-hop

Do not stop at the first extra URL. Chain: landing → linked partners/conversion pages → nested tables/accordions. Treat a hop error (`ERR_HTTP2_*`, empty title `www.…`) as “try the next URL,” not `chart_not_found`. Keep HTML from every hop.

### Trick header tracking

Sites (Akamai on JAL/Cathay) track more than User-Agent: header order, `sec-ch-ua` / Client Hints, Accept / Accept-Language, HTTP/2 fingerprint. httpx defaults do not match Chrome. Align HTTP headers with the real browser profile, or skip HTTP on vendors that already 403. Log request headers and `Set-Cookie` names used for tracking (`_abck`, `bm_sz`, `ak_bmsc`) without treating cookies alone as a block when the chart is present.

### Firefox and Safari

Chrome-only is a single fingerprint. Add **Firefox** as a first-class browser (not only Tor). Add **Safari / WebKit** as a later probe (Playwright WebKit or a Mac-side WebKit driver — Safari does not run on the Ubuntu VPS). Rotate browser per site / per retry, not one binary for the whole job.

### Rotating IP and residential proxies

VPS IPv4 is one datacenter address for the whole batch. Use **rotating residential proxies** so each site (or each hop) can get a new household ISP IP, US-sticky for Capital One / Cathay. Log `egress_ip` per attempt; rotate on `Access Denied`, Akamai challenge, or HTTP/2 protocol errors. Tor stays experimental; residential is the next egress tier, not another datacenter.

### Library: SwiftChannel

Evaluate **SwiftChannel** as the client/session layer for proxy rotation, sticky sessions, and browser-aligned requests (HTTP + Selenium/WebDriver). Keep credentials in `.env` on the VPS only. If the package name resolves differently at install time, record the actual import here and do not guess a substitute.

### No confidence

- Remove `CHART_CONFIDENCE_THRESHOLD` as a deliverable gate.
- Success = non-empty `chart_rows` (or equivalent partner/rate pairs).
- Keep a debug score in JSON if useful, but never fail a scrape because it is below 0.45.
- Update unit tests that assert `confidence >= 0.45` for deliverable.
