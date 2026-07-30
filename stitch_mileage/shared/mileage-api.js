/**
 * Thin Stitch ↔ FastAPI bridge for Run Search.
 * Requires Vite proxy (ui/vite.config.ts) and uvicorn on :8000.
 *
 * Usage from quick search:
 *   const result = await MileageAPI.runQuote({ origin, dest, cabin, miles, currency, card, travelWindow });
 */
(function (global) {
  const API_BASE = "";

  const WINDOW_TO_DAYS = {
    flexible: [0, 90],
    non_holiday: [14, 120],
    next_60: [0, 60],
    next_90: [0, 90],
    peak_summer: null, // resolved seasonally below
    holidays: null,
  };

  function iso(d) {
    return d.toISOString().slice(0, 10);
  }

  function datesForWindow(id) {
    const today = new Date();
    const start = new Date(today);
    const end = new Date(today);
    if (id === "peak_summer") {
      const y = today.getMonth() >= 8 ? today.getFullYear() + 1 : today.getFullYear();
      return { start_date: `${y}-06-01`, end_date: `${y}-08-31` };
    }
    if (id === "holidays") {
      const y = today.getMonth() === 11 && today.getDate() > 25
        ? today.getFullYear() + 1
        : today.getFullYear();
      return { start_date: `${y}-12-15`, end_date: `${y}-12-31` };
    }
    const span = WINDOW_TO_DAYS[id] || WINDOW_TO_DAYS.flexible;
    start.setDate(start.getDate() + span[0]);
    end.setDate(end.getDate() + span[1]);
    return { start_date: iso(start), end_date: iso(end) };
  }

  function guessAirport(text) {
    const t = (text || "").trim().toUpperCase();
    const m = t.match(/\b([A-Z]{3})\b/);
    if (m) return m[1];
    // common city aliases for beta demos
    const map = {
      ISTANBUL: "IST",
      TOKYO: "NRT",
      NYC: "JFK",
      "NEW YORK": "JFK",
      LONDON: "LHR",
      PARIS: "CDG",
      ROME: "FCO",
      MILAN: "MXP",
      ITALY: "FCO",
      "LOS ANGELES": "LAX",
      "SAN FRANCISCO": "SFO",
      "SAN JOSE": "SJC",
      "OAKLAND": "OAK",
      "SAN DIEGO": "SAN",
      "SEATTLE": "SEA",
      "PORTLAND": "PDX",
    };
    for (const [k, v] of Object.entries(map)) {
      if (t.includes(k)) return v;
    }
    return t.slice(0, 3);
  }

  /** Origin must be an exact IATA airport code (3 letters). */
  function requireAirportCode(text) {
    const t = (text || "").trim().toUpperCase();
    if (/^[A-Z]{3}$/.test(t)) return t;
    throw new Error("Origin must be a 3-letter airport code (e.g. LAX, SFO)");
  }

  /** Destination: airport code OR city/country free text (resolved when possible). */
  function resolveDestination(text) {
    const raw = (text || "").trim();
    if (!raw) throw new Error("Enter a destination (airport code or city)");
    const code = guessAirport(raw);
    if (!code || code.length !== 3 || !/^[A-Z]{3}$/.test(code)) {
      throw new Error("Could not resolve destination — try a city name or IATA code");
    }
    return code;
  }

  const HOME_ORIGIN_KEY = "mileage:home_origin";

  function getHomeOrigin() {
    const saved = (localStorage.getItem(HOME_ORIGIN_KEY) || "").trim().toUpperCase();
    if (/^[A-Z]{3}$/.test(saved)) return saved;
    return null; // first-time user
  }

  function setHomeOrigin(code) {
    const origin = requireAirportCode(code);
    localStorage.setItem(HOME_ORIGIN_KEY, origin);
    return origin;
  }

  async function startRedemption(body) {
    const res = await fetch(`${API_BASE}/redemptions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`Start failed (${res.status})`);
    return res.json();
  }

  async function pollStatus(runId, onProgress) {
    for (;;) {
      const res = await fetch(`${API_BASE}/status/${runId}`);
      if (!res.ok) throw new Error(`Status failed (${res.status})`);
      const status = await res.json();
      if (onProgress) onProgress(status);
      if (status.status === "complete" || status.status === "error") return status;
      await new Promise((r) => setTimeout(r, 400));
    }
  }

  async function runQuote(opts) {
    const origin = requireAirportCode(opts.origin || getHomeOrigin());
    const dest = resolveDestination(opts.dest);
    const dates = datesForWindow(opts.travelWindow || "flexible");
    const body = {
      origin,
      dest,
      cabin: opts.cabin || "business",
      currency: opts.currency || "capital_one",
      miles: Number(opts.miles) || 90000,
      card: opts.card || "venture_x",
      travel_window: opts.travelWindow || "flexible",
      start_date: dates.start_date,
      end_date: dates.end_date,
    };
    const { run_id } = await startRedemption(body);
    const final = await pollStatus(run_id, opts.onProgress);
    if (final.status === "error") {
      throw new Error(final.message || final.error || "Quote failed");
    }
    // Persist for verdict / save / wishlist screens
    const payload = {
      saved_at: new Date().toISOString(),
      request: body,
      result: final.result,
      run_id,
    };
    localStorage.setItem("mileage:last_quote", JSON.stringify(payload));
    return payload;
  }

  function loadLastQuote() {
    try {
      return JSON.parse(localStorage.getItem("mileage:last_quote") || "null");
    } catch {
      return null;
    }
  }

  function listKey(kind) {
    return kind === "wishlist" ? "mileage:wishlist" : "mileage:saved";
  }

  function listItems(kind) {
    try {
      return JSON.parse(localStorage.getItem(listKey(kind)) || "[]");
    } catch {
      return [];
    }
  }

  function upsertItem(kind, item) {
    const items = listItems(kind).filter((x) => x.id !== item.id);
    items.unshift(item);
    localStorage.setItem(listKey(kind), JSON.stringify(items.slice(0, 40)));
    return items;
  }

  function saveFromLast(kind) {
    const last = loadLastQuote();
    if (!last || !last.result) throw new Error("No quote to save — run a search first");
    const req = last.request || {};
    const item = {
      id: last.run_id || `${req.origin}-${req.dest}-${Date.now()}`,
      kind, // saved | wishlist
      monitor: kind === "wishlist",
      origin: req.origin,
      dest: req.dest,
      cabin: req.cabin,
      travel_window: req.travel_window,
      verdict: last.result.verdict,
      rationale: last.result.rationale,
      best_transfer: last.result.best_transfer,
      portal_cpp: last.result.portal_cpp,
      created_at: new Date().toISOString(),
      last_checked_at: new Date().toISOString(),
      result: last.result,
    };
    upsertItem(kind, item);
    // Remove from the other list if present
    const other = kind === "wishlist" ? "saved" : "wishlist";
    localStorage.setItem(
      listKey(other),
      JSON.stringify(listItems(other).filter((x) => x.id !== item.id)),
    );
    return item;
  }

  global.MileageAPI = {
    runQuote,
    loadLastQuote,
    listItems,
    saveFromLast,
    guessAirport,
    resolveDestination,
    requireAirportCode,
    getHomeOrigin,
    setHomeOrigin,
    datesForWindow,
  };
})(window);
