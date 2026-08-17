export type PipelineStep = "route" | "gathering" | "crosscheck" | "redemptions";
export type RunStatus = "pending" | "running" | "complete" | "error";

export type CurrencyId =
  | "capital_one"
  | "amex_mr"
  | "chase_ur"
  | "citi_typ"
  | "bilt"
  | "wells_fargo"
  | "marriott_bonvoy"
  | "hilton";

export interface RedemptionRequest {
  origin: string;
  dest: string;
  cabin: "economy" | "premium_economy" | "business" | "first";
  currency: CurrencyId | string;
  miles: number;
  card: "venture" | "venture_x";
  travel_window?: string;
  start_date?: string;
  end_date?: string;
}

/** Three states, because two is a lie (see domain/models.py AwardSpace).
 *  `no_space` = a live source WAS queried and found nothing.
 *  `space_unknown` = nothing looked. Never render this as "unavailable". */
export type AwardSpaceState = "space_confirmed" | "no_space" | "space_unknown";

export interface Gate {
  kind: "hard" | "acquirable" | "accountAge";
  description: string;
  card_ids: string[];
  annual_fee_usd?: number | null;
  approval_days?: number | null;
}

export interface PathOption {
  label: string;
  kind: "portal" | "transfer";
  /** Always present — the two numbers a user actually pays. */
  source_points: number;
  price_paid_cents: number;
  price_paid_usd: number;
  taxes_cents: number;
  fuel_cents: number;
  fuel_policy?: string | null;
  /** null when no market fare existed to compute it from. NOT the same as 0. */
  cpp: number | null;
  cash_cents?: number | null;
  program?: string | null;
  operating_carrier?: string | null;
  carrier_name?: string | null;
  cabin: string;
  space: AwardSpaceState;
  space_label: string;
  transfer_hops: number;
  settlement_minutes: number;
  affordable: boolean;
  confidence: number;
  flags: string[];
  reason: string;
  gates: Gate[];
  currency?: string | null;
}

export interface GatedSummaryEntry {
  requirement: string;
  kind: string;
  routes: number;
  annual_fee_usd?: number | null;
  approval_days?: number | null;
}

export interface LayerCoverage {
  eligible_providers: string[];
  attempts: number;
  queried: boolean;
  quotes: number;
  skipped: Record<string, number>;
  degraded: boolean;
}

export interface QuoteResult {
  route: string;
  verdict?: string;
  rationale?: string;
  /** The RULE that produced the label, in words. */
  reason?: string;
  flags?: string[];
  /** True when no market fare existed — cpp is omitted, everything else stands. */
  degraded?: boolean;
  fare_cents?: number | null;
  fare_flags?: string[];
  portal_cpp?: number | null;
  /** False means NO availability source ran. Do not render as "no seats". */
  space_checked?: boolean;
  award_space?: {
    confirmed: Array<{
      program: string;
      miles: number;
      seats_available: number;
      carrier?: string | null;
    }>;
    checked_none: string[];
    unknown: string[];
  };
  best_transfer?: PathOption;
  options?: PathOption[];
  /** §6.3 — never hidden, shown separately as "3 routes require a … card ›". */
  gated_options?: PathOption[];
  gated_summary?: GatedSummaryEntry[];
  /** The list is capped at 10; say how many were considered. */
  options_considered?: number;
  options_shown?: number;
  carriers_serving?: string[];
  coverage?: Record<string, LayerCoverage>;
  live_award_space?: Array<{
    program: string;
    miles: number;
    seats_available: number;
    flags: string[];
  }>;
  error?: string;
  message?: string;
}

export interface RunStatusResponse {
  run_id: string;
  status: RunStatus;
  step: PipelineStep;
  steps_done: PipelineStep[];
  result?: QuoteResult;
  error?: string;
  message?: string;
}

export type ScrapeStatus = "ok" | "warn" | "fail";

export interface ScrapeTarget {
  name: string;
  url: string;
  role: "primary" | "fallback";
  format: string;
  provides: "chart" | "award";
  trust: number;
  status: ScrapeStatus;
  detail: string;
  rows: number;
  program?: string | null;
  resolved?: string | null;
  reclassified: boolean;
  sample: Array<Record<string, unknown>>;
  /** Bypass layer 1 — what blocked / challenged the fetch, if anything. */
  block_type?: string | null;
  block_signals?: string[];
}

export interface ScrapeProgramEntry {
  name: string;
  status: ScrapeStatus;
  detail: string;
}

export interface ScrapeProgram {
  program: string;
  has_working_primary: boolean;
  primaries: ScrapeProgramEntry[];
  fallbacks: ScrapeProgramEntry[];
}

export interface ScrapeSummary {
  total: number;
  programs: number;
  all_primaries_ok: boolean;
  primary_ok: number;
  primary_warn: number;
  primary_fail: number;
  fallback_ok: number;
  fallback_warn: number;
}

export interface LiveScrapeDiscoveryResult {
  row_count: number;
  email_docs: number;
  blog_new: number;
  transcript_new: number;
  email_links_followed: number;
  by_intake: Record<string, number>;
  stale_programs: string[];
  used_fixtures: boolean;
  detail: string;
}

export interface DailyScrapeResponse {
  found: boolean;
  storage?: string | null;
  storage_backend?: string | null;
  completed_at?: string | null;
  stored_at?: string | null;
  discovery?: LiveScrapeDiscoveryResult | null;
  scrape?: {
    summary?: ScrapeSummary;
  } | null;
}

export interface LiveScrapeResponse {
  offline: boolean;
  targets: ScrapeTarget[];
  programs: ScrapeProgram[];
  summary: ScrapeSummary;
  discovery?: LiveScrapeDiscoveryResult | null;
}

export interface DiscoveryChannel {
  kind: "email" | "blog" | "youtube";
  name: string;
  url?: string | null;
  trust: number;
  ready: boolean;
  command: string;
  detail: string;
}

export interface ProviderPath {
  name: string;
  health: string;
  trust: number;
  layers: string[];
  disabled: boolean;
  monthly_limit?: number | null;
  config_hint?: string | null;
  note?: string | null;
}

export interface DiscoveredChartsMeta {
  updated_at?: string | null;
  row_count: number;
  by_intake: Record<string, number>;
  stale_programs: string[];
}

export interface ScrapeInventorySummary {
  chart_targets: number;
  discovery_channels: number;
  discovery_ready: number;
  providers: number;
  providers_healthy: number;
  discovered_rows: number;
}

export interface ScrapeInventoryResponse {
  discovery: DiscoveryChannel[];
  providers: ProviderPath[];
  discovered: DiscoveredChartsMeta;
  summary: ScrapeInventorySummary;
}
