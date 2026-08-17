import { FormEvent, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import AirportInput from "./AirportInput";
import DisconnectedPage from "./DisconnectedPage";
import LiveScrapePage from "./LiveScrapePage";
import { isKnownAirport, resolveAirport } from "./airports";
import {
  ApiConnectionError,
  checkHealth,
  formatCpp,
  formatDollars,
  formatUsd,
  parseMiles,
  pollUntilComplete,
  startRedemption,
} from "./api";
import {
  CABINS,
  CARD_PRODUCTS,
  CURRENCIES,
  TRAVEL_WINDOWS,
  datesForTravelWindow,
  currencyLabel,
  currencyShort,
  type Cabin,
  type CardProduct,
  type CurrencyId,
  type TravelWindowId,
} from "./plannerOptions";
import type { PipelineStep, QuoteResult, RunStatusResponse } from "./types";

const STEPS: { key: PipelineStep; label: string; n: number }[] = [
  { key: "route", label: "Route", n: 1 },
  { key: "gathering", label: "Gathering", n: 2 },
  { key: "crosscheck", label: "Cross-check", n: 3 },
  { key: "redemptions", label: "Redemptions", n: 4 },
];

type DemoKey = "A" | "B";
type View = "optimizer" | "debug";

const DEMOS: Record<
  DemoKey,
  {
    label: string;
    origin: string;
    dest: string;
    cabin: Cabin;
    miles: string;
    currency: CurrencyId;
  }
> = {
  A: {
    label: "Demo A — Honest floor",
    origin: "LAX",
    dest: "JFK",
    cabin: "economy",
    miles: "20,000",
    currency: "capital_one",
  },
  B: {
    label: "Demo B — Hidden value",
    origin: "LAX",
    dest: "IST",
    cabin: "business",
    miles: "90,000",
    currency: "capital_one",
  },
};

function stepClass(
  stepKey: PipelineStep,
  activeStep: PipelineStep | null,
  stepsDone: PipelineStep[],
): string {
  if (activeStep === stepKey) return "step active";
  const order = STEPS.map((s) => s.key);
  const activeIdx = activeStep ? order.indexOf(activeStep) : -1;
  const stepIdx = order.indexOf(stepKey);
  if (stepsDone.includes(stepKey) && stepIdx < activeIdx) return "step done";
  if (
    stepsDone.includes(stepKey) &&
    activeStep === "redemptions" &&
    stepKey !== "redemptions"
  ) {
    return "step done";
  }
  return "step";
}

function verdictHeadline(verdict?: string): string {
  switch (verdict) {
    case "portal_only":
      return "Portal is your floor";
    case "comparable":
      return "Comparable to portal";
    case "best":
    case "tentative_best":
      return "Best verified route";
    default:
      return "Result";
  }
}

function verdictClass(verdict?: string): string {
  if (verdict === "best" || verdict === "tentative_best") return "verdict best";
  return "verdict portal";
}

function buildSubline(result: QuoteResult): string {
  const parts: string[] = [];
  if (result.flags?.length) {
    parts.push(result.flags.join(" · "));
  }
  if (result.live_award_space?.length) {
    parts.push(`${result.live_award_space.length} live seat source(s)`);
  } else if (
    result.verdict === "best" ||
    result.verdict === "tentative_best"
  ) {
    parts.push("chart verified");
  }
  if (result.fare_cents) {
    parts.push(`cash fare ${formatDollars(result.fare_cents)}`);
  }
  return parts.join(" · ") || "Verified across sources";
}

function matchesDemo(
  key: DemoKey,
  origin: string,
  dest: string,
  cabin: string,
  miles: string,
  currency: CurrencyId,
): boolean {
  const demo = DEMOS[key];
  return (
    resolveAirport(origin)?.code === demo.origin &&
    resolveAirport(dest)?.code === demo.dest &&
    cabin === demo.cabin &&
    parseMiles(miles) === parseMiles(demo.miles) &&
    currency === demo.currency
  );
}

function ToggleSection({
  title,
  subtitle,
  open,
  onToggle,
  children,
}: {
  title: string;
  subtitle?: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className={`toggle-section${open ? " open" : ""}`}>
      <button
        type="button"
        className="toggle-section-head"
        onClick={onToggle}
        aria-expanded={open}
      >
        <span>
          <span className="toggle-section-title">{title}</span>
          {subtitle && !open && (
            <span className="toggle-section-sub">{subtitle}</span>
          )}
        </span>
        <span className="toggle-chevron" aria-hidden>
          {open ? "−" : "+"}
        </span>
      </button>
      {open && <div className="toggle-section-body">{children}</div>}
    </section>
  );
}

function ChipGroup<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled,
}: {
  label: string;
  options: { id: T; label: string; hint?: string }[];
  value: T;
  onChange: (id: T) => void;
  disabled?: boolean;
}) {
  return (
    <div className="chip-group">
      <span className="chip-group-label">{label}</span>
      <div className="chip-row" role="group" aria-label={label}>
        {options.map((opt) => (
          <button
            key={opt.id}
            type="button"
            className={`chip${value === opt.id ? " selected" : ""}`}
            disabled={disabled}
            onClick={() => onChange(opt.id)}
            title={opt.hint}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>("optimizer");
  const [origin, setOrigin] = useState("LAX");
  const [dest, setDest] = useState("IST");
  const [cabin, setCabin] = useState<Cabin>("business");
  const [currency, setCurrency] = useState<CurrencyId>("capital_one");
  const [card, setCard] = useState<CardProduct>("venture_x");
  const [miles, setMiles] = useState("90,000");
  const [travelWindow, setTravelWindow] = useState<TravelWindowId>("next_60");
  const [showBonusesOnly, setShowBonusesOnly] = useState(false);
  const [showMultiHop, setShowMultiHop] = useState(true);
  const [openSections, setOpenSections] = useState({
    where: true,
    points: true,
    comfort: false,
    advanced: false,
  });
  const [selectedDemo, setSelectedDemo] = useState<DemoKey | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeStep, setActiveStep] = useState<PipelineStep | null>("route");
  const [stepsDone, setStepsDone] = useState<PipelineStep[]>([]);
  const [result, setResult] = useState<QuoteResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [apiConnected, setApiConnected] = useState<boolean | null>(null);
  const [checkingConnection, setCheckingConnection] = useState(false);

  const probeConnection = useCallback(async () => {
    setCheckingConnection(true);
    const ok = await checkHealth();
    setApiConnected(ok);
    setCheckingConnection(false);
    return ok;
  }, []);

  useEffect(() => {
    void probeConnection();
  }, [probeConnection]);

  useEffect(() => {
    const intervalMs = apiConnected ? 15000 : 4000;
    const id = window.setInterval(() => {
      void probeConnection();
    }, intervalMs);
    return () => window.clearInterval(id);
  }, [apiConnected, probeConnection]);

  useEffect(() => {
    if (!import.meta.hot) return;
    const onDisconnect = () => setApiConnected(false);
    import.meta.hot.on("vite:ws:disconnect", onDisconnect);
    return () => {
      import.meta.hot?.off("vite:ws:disconnect", onDisconnect);
    };
  }, []);

  const originCode = resolveAirport(origin)?.code ?? origin;
  const destCode = resolveAirport(dest)?.code ?? dest;
  const airportsValid = isKnownAirport(origin) && isKnownAirport(dest);
  const travelLabel =
    TRAVEL_WINDOWS.find((w) => w.id === travelWindow)?.label ?? travelWindow;

  const displayPath = useMemo(() => {
    if (!result) return null;
    if (
      result.best_transfer &&
      (result.verdict === "best" || result.verdict === "tentative_best")
    ) {
      return result.best_transfer.label.replace(
        /^Capital One/,
        currencyLabel(currency),
      );
    }
    const opt = CURRENCIES.find((c) => c.id === currency);
    if (opt?.hasPortal) return `${opt.label} portal`;
    return `${currencyLabel(currency)} (no portal floor yet)`;
  }, [result, currency]);

  const displayCpp = useMemo(() => {
    if (!result) return null;
    if (
      result.best_transfer &&
      (result.verdict === "best" || result.verdict === "tentative_best")
    ) {
      return result.best_transfer.cpp;
    }
    // `?? 0` would turn "no fare, so no cents-per-point" into a confident 0.0¢.
    return result.portal_cpp ?? null;
  }, [result]);

  const filteredOptions = useMemo(() => {
    if (!result?.options) return [];
    return result.options.filter((opt) => {
      if (showBonusesOnly && !opt.flags.some((f) => f.includes("bonus"))) {
        return opt.kind === "portal";
      }
      if (!showMultiHop && opt.flags.includes("multi_hop")) return false;
      return true;
    });
  }, [result, showBonusesOnly, showMultiHop]);

  function toggleSection(key: keyof typeof openSections) {
    setOpenSections((s) => ({ ...s, [key]: !s[key] }));
  }

  function clearDemoSelection() {
    setSelectedDemo(null);
  }

  function applyDemo(key: DemoKey) {
    const demo = DEMOS[key];
    setOrigin(demo.origin);
    setDest(demo.dest);
    setCabin(demo.cabin);
    setMiles(demo.miles);
    setCurrency(demo.currency);
    setSelectedDemo(key);
    setResult(null);
    setError(null);
    setActiveStep("route");
    setStepsDone([]);
    setOpenSections({ where: true, points: true, comfort: false, advanced: false });
  }

  function handleOriginChange(code: string) {
    setOrigin(code);
    if (matchesDemo("A", code, dest, cabin, miles, currency)) setSelectedDemo("A");
    else if (matchesDemo("B", code, dest, cabin, miles, currency))
      setSelectedDemo("B");
    else clearDemoSelection();
  }

  function handleDestChange(code: string) {
    setDest(code);
    if (matchesDemo("A", origin, code, cabin, miles, currency)) setSelectedDemo("A");
    else if (matchesDemo("B", origin, code, cabin, miles, currency))
      setSelectedDemo("B");
    else clearDemoSelection();
  }

  function handleCabinChange(next: Cabin) {
    setCabin(next);
    clearDemoSelection();
  }

  function handleCurrencyChange(next: CurrencyId) {
    setCurrency(next);
    const preset = CURRENCIES.find((c) => c.id === next);
    if (preset) setMiles(preset.defaultMiles.toLocaleString("en-US"));
    clearDemoSelection();
  }

  function handleMilesChange(raw: string) {
    setMiles(raw);
    if (matchesDemo("A", origin, dest, cabin, raw, currency)) setSelectedDemo("A");
    else if (matchesDemo("B", origin, dest, cabin, raw, currency))
      setSelectedDemo("B");
    else clearDemoSelection();
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!airportsValid) {
      setError("Choose a valid airport from the suggestions.");
      return;
    }

    setLoading(true);
    setResult(null);
    setError(null);
    setActiveStep("route");
    setStepsDone([]);

    try {
      const dates = datesForTravelWindow(travelWindow);
      const { run_id } = await startRedemption({
        origin: originCode,
        dest: destCode,
        cabin,
        currency,
        miles: parseMiles(miles),
        card: currency === "capital_one" ? card : "venture_x",
        start_date: dates.start_date,
        end_date: dates.end_date,
        travel_window: travelWindow,
      });

      const finalStatus: RunStatusResponse = await pollUntilComplete(
        run_id,
        (status) => {
          setActiveStep(status.step);
          setStepsDone(status.steps_done);
        },
      );

      if (finalStatus.status === "error") {
        setError(finalStatus.message ?? finalStatus.error ?? "Run failed");
        if (finalStatus.result) setResult(finalStatus.result);
        return;
      }

      if (finalStatus.result) {
        setResult(finalStatus.result);
      }
    } catch (err) {
      if (err instanceof ApiConnectionError) {
        setApiConnected(false);
        setError(null);
        return;
      }
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
      setActiveStep("redemptions");
    }
  }

  if (apiConnected === false) {
    return (
      <DisconnectedPage
        onRetry={() => {
          void probeConnection();
        }}
        checking={checkingConnection}
      />
    );
  }

  return (
    <>
      <header>
        <div className="brand">
          <span className="mark">✦</span> Mileage
        </div>
        <div className="header-right">
          <nav className="page-nav" aria-label="Pages">
            <button
              type="button"
              className={`page-tab${view === "optimizer" ? " active" : ""}`}
              onClick={() => setView("optimizer")}
            >
              Optimizer
            </button>
            <a className="page-tab" href="/stitch/">
              Stitch
            </a>
            <button
              type="button"
              className={`page-tab${view === "debug" ? " active" : ""}`}
              onClick={() => setView("debug")}
            >
              Debug UI
            </button>
          </nav>
          {view === "optimizer" && (
            <nav className="steps" aria-label="Progress">
              {STEPS.map((s) => (
                <span
                  key={s.key}
                  className={stepClass(s.key, activeStep, stepsDone)}
                >
                  <span className="n">{s.n}</span>
                  {s.label}
                </span>
              ))}
            </nav>
          )}
        </div>
      </header>

      {view === "debug" && <LiveScrapePage />}

      {view === "optimizer" && (
        <main>
          <h1 className="wordmark">Mileage</h1>
          <p className="tagline">
            Where do you want to go — and when? We compare transfer paths and
            portal floors with verified sources.
          </p>

          <form
            className="card planner-card"
            aria-label="Plan your redemption"
            onSubmit={handleSubmit}
          >
            <ToggleSection
              title="1. Where & when"
              subtitle={`${originCode} → ${destCode} · ${travelLabel}`}
              open={openSections.where}
              onToggle={() => toggleSection("where")}
            >
              <div className="row">
                <AirportInput
                  id="from"
                  label="From"
                  placeholder="LAX"
                  value={origin}
                  excludeCode={destCode}
                  onChange={handleOriginChange}
                />
                <AirportInput
                  id="to"
                  label="To"
                  placeholder="IST"
                  value={dest}
                  excludeCode={originCode}
                  onChange={handleDestChange}
                />
              </div>
              <div className="field select" style={{ marginTop: 14 }}>
                <label htmlFor="travel-window">Rough travel window</label>
                <select
                  id="travel-window"
                  value={travelWindow}
                  onChange={(e) =>
                    setTravelWindow(e.target.value as TravelWindowId)
                  }
                >
                  {TRAVEL_WINDOWS.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.label}
                    </option>
                  ))}
                </select>
              </div>
              <p className="field-hint">
                Travel dates refine live seat search once seats.aero is wired;
                charts and transfer math use route + cabin today.
              </p>
            </ToggleSection>

            <ToggleSection
              title="2. Your points"
              subtitle={`${currencyLabel(currency)} · ${miles} pts`}
              open={openSections.points}
              onToggle={() => toggleSection("points")}
            >
              <ChipGroup
                label="Card / currency"
                options={CURRENCIES.map((c) => ({
                  id: c.id,
                  label: c.label,
                }))}
                value={currency}
                onChange={handleCurrencyChange}
                disabled={loading}
              />
              <div className="row" style={{ marginTop: 14 }}>
                <div className="field">
                  <label htmlFor="points">
                    {currencyLabel(currency)} balance
                  </label>
                  <input
                    id="points"
                    type="text"
                    placeholder="85,000"
                    value={miles}
                    onChange={(e) => handleMilesChange(e.target.value)}
                    required
                  />
                </div>
              </div>
              {currency === "capital_one" && (
                <ChipGroup
                  label="Capital One product (portal floor)"
                  options={CARD_PRODUCTS.map((c) => ({
                    id: c.id,
                    label: `${c.label} (${c.cpp})`,
                  }))}
                  value={card}
                  onChange={setCard}
                  disabled={loading}
                />
              )}
            </ToggleSection>

            <ToggleSection
              title="3. Cabin"
              subtitle={CABINS.find((c) => c.id === cabin)?.label ?? cabin}
              open={openSections.comfort}
              onToggle={() => toggleSection("comfort")}
            >
              <ChipGroup
                label="Cabin class"
                options={CABINS.map((c) => ({ id: c.id, label: c.label }))}
                value={cabin}
                onChange={handleCabinChange}
                disabled={loading}
              />
            </ToggleSection>

            <ToggleSection
              title="4. Advanced"
              subtitle="Filters & demos"
              open={openSections.advanced}
              onToggle={() => toggleSection("advanced")}
            >
              <div className="toggle-row">
                <label className="switch-label">
                  <input
                    type="checkbox"
                    checked={showMultiHop}
                    onChange={(e) => setShowMultiHop(e.target.checked)}
                  />
                  Show multi-hop paths (e.g. bonus → partner)
                </label>
              </div>
              <div className="toggle-row">
                <label className="switch-label">
                  <input
                    type="checkbox"
                    checked={showBonusesOnly}
                    onChange={(e) => setShowBonusesOnly(e.target.checked)}
                  />
                  Highlight transfer-bonus paths only
                </label>
              </div>
              <div className="demo-row">
                {(Object.keys(DEMOS) as DemoKey[]).map((key) => {
                  const selected = selectedDemo === key;
                  return (
                    <button
                      key={key}
                      type="button"
                      className={`demo-btn${selected ? " selected" : ""}`}
                      disabled={loading}
                      onClick={() => applyDemo(key)}
                    >
                      {DEMOS[key].label}
                    </button>
                  );
                })}
              </div>
            </ToggleSection>

            <button
              className="cta"
              type="submit"
              disabled={loading || !airportsValid}
            >
              {loading ? "Running pipeline…" : "Find best routes"}
            </button>
            <p className="note">
              Aggregator charts + curated ratios run in parallel for{" "}
              <b>{currencyShort(currency)}</b> — we only name a winner when the
              numbers are verified.
            </p>
          </form>

          {error && (
            <div className="error-box" role="alert">
              {error}
            </div>
          )}

          {result && result.verdict && displayPath && displayCpp !== null && (
            <>
              <section
                className={verdictClass(result.verdict)}
                aria-label="Verdict"
              >
                <div>
                  <div className="label">{verdictHeadline(result.verdict)}</div>
                  <div className="path">{displayPath}</div>
                  <div className="sub">{buildSubline(result)}</div>
                  {result.rationale && (
                    <div className="sub">{result.rationale}</div>
                  )}
                </div>
                <div className="cpp">
                  {formatCpp(displayCpp)}
                  <small>per {currencyShort(currency)} pt</small>
                </div>
              </section>

              {filteredOptions.length > 0 && (
                <section className="options" aria-label="Ranked redemptions">
                  <h2>Ranked redemptions</h2>
                  {filteredOptions.map((opt) => {
                    const highlight =
                      result.best_transfer &&
                      opt.label === result.best_transfer.label &&
                      (result.verdict === "best" ||
                        result.verdict === "tentative_best");
                    return (
                      <div
                        key={opt.label}
                        className={`option-row${highlight ? " highlight" : ""}`}
                      >
                        <div>
                          <div>
                            {opt.label.replace(
                              /^Capital One/,
                              currencyLabel(currency),
                            )}
                          </div>
                          <div className="meta">
                            {opt.source_points.toLocaleString()} pts · conf{" "}
                            {opt.confidence.toFixed(2)}
                            {!opt.affordable ? " · need more points" : ""}
                            {opt.flags.includes("transfer_bonus") && (
                              <span className="flag-chip bonus">bonus</span>
                            )}
                            {opt.flags.includes("multi_hop") && (
                              <span className="flag-chip hop">multi-hop</span>
                            )}
                            {/* Three states. `space_unknown` must never read as
                                "no seats" — nothing checked. */}
                            {opt.space === "space_confirmed" && (
                              <span className="flag-chip">seats confirmed</span>
                            )}
                            {opt.space === "space_unknown" && (
                              <span className="flag-chip">
                                availability not checked
                              </span>
                            )}
                            {opt.flags.length
                              ? ` · ${opt.flags.filter((f) => !["transfer_bonus", "multi_hop"].includes(f)).join(", ")}`
                              : ""}
                          </div>
                          {opt.reason && (
                            <div className="meta">{opt.reason}</div>
                          )}
                        </div>
                        {/* §6.2 — price paid leads: the cash actually leaving
                            your pocket. cpp is a secondary line and is omitted
                            entirely when no market fare existed. */}
                        <div>
                          {formatUsd(opt.price_paid_usd)}
                          <small>
                            {opt.cpp !== null && opt.cpp !== undefined
                              ? formatCpp(opt.cpp)
                              : "cpp n/a"}
                          </small>
                        </div>
                      </div>
                    );
                  })}
                  {/* The list is capped at ten. Say so — a truncated list that
                      looks exhaustive is its own kind of dishonesty. */}
                  {result.options_considered != null &&
                    result.options_shown != null &&
                    result.options_considered > result.options_shown && (
                      <div className="meta">
                        Showing {result.options_shown} of{" "}
                        {result.options_considered} bookable routes.
                      </div>
                    )}
                </section>
              )}

              {/* §6.3 — gated routes are NEVER hidden. Knowing which card
                  unlocks a trip you're actually trying to take is the useful
                  part, and it only works if the route is visible. */}
              {result.gated_summary && result.gated_summary.length > 0 && (
                <section className="options" aria-label="Routes requiring a card">
                  <h2>Routes you can&rsquo;t book yet</h2>
                  {result.gated_summary.map((entry) => (
                    <div key={entry.requirement} className="option-row">
                      <div>
                        <div>
                          {entry.routes} route{entry.routes === 1 ? "" : "s"}{" "}
                          {entry.requirement}
                        </div>
                        {entry.annual_fee_usd != null && (
                          <div className="meta">
                            ${entry.annual_fee_usd}/yr
                            {entry.approval_days
                              ? ` · ~${entry.approval_days} days to approve`
                              : ""}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                  {(result.gated_options ?? []).slice(0, 5).map((opt) => (
                    <div key={`gated-${opt.label}`} className="option-row">
                      <div>
                        <div>{opt.label}</div>
                        <div className="meta">{opt.reason}</div>
                      </div>
                      <div>
                        {formatUsd(opt.price_paid_usd)}
                        <small>{opt.source_points.toLocaleString()} pts</small>
                      </div>
                    </div>
                  ))}
                </section>
              )}

              {/* A silent default that reads as a confirmed negative is the
                  worst failure mode available here. Say when nothing looked. */}
              {result.space_checked === false && (
                <section className="options" aria-label="Availability">
                  <div className="meta">
                    Award availability was not checked for this route. That is
                    not a report that seats are unavailable.
                  </div>
                </section>
              )}
            </>
          )}
        </main>
      )}
    </>
  );
}
