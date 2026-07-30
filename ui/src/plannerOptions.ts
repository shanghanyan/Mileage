/** Shared planner constants — currencies/cabins the backend accepts today. */

export type Cabin =
  | "economy"
  | "premium_economy"
  | "business"
  | "first";

export type CardProduct = "venture" | "venture_x";

export type CurrencyId =
  | "capital_one"
  | "amex_mr"
  | "chase_ur"
  | "citi_typ"
  | "bilt"
  | "wells_fargo"
  | "marriott_bonvoy"
  | "hilton";

export interface CurrencyOption {
  id: CurrencyId;
  label: string;
  short: string;
  defaultMiles: number;
  /** Honest travel-portal floor exists for this currency. */
  hasPortal: boolean;
}

export const CURRENCIES: CurrencyOption[] = [
  {
    id: "capital_one",
    label: "Capital One",
    short: "C1",
    defaultMiles: 90_000,
    hasPortal: true,
  },
  {
    id: "chase_ur",
    label: "Chase UR",
    short: "UR",
    defaultMiles: 100_000,
    hasPortal: true,
  },
  {
    id: "amex_mr",
    label: "Amex MR",
    short: "MR",
    defaultMiles: 100_000,
    hasPortal: true,
  },
  {
    id: "citi_typ",
    label: "Citi ThankYou",
    short: "TYP",
    defaultMiles: 80_000,
    hasPortal: true,
  },
  {
    id: "bilt",
    label: "Bilt",
    short: "Bilt",
    defaultMiles: 50_000,
    hasPortal: true,
  },
  {
    id: "wells_fargo",
    label: "Wells Fargo",
    short: "WF",
    defaultMiles: 60_000,
    hasPortal: true,
  },
  {
    id: "marriott_bonvoy",
    label: "Marriott Bonvoy",
    short: "Bonvoy",
    defaultMiles: 150_000,
    hasPortal: false,
  },
  {
    id: "hilton",
    label: "Hilton Honors",
    short: "Hilton",
    defaultMiles: 120_000,
    hasPortal: false,
  },
];

export const CABINS: { id: Cabin; label: string }[] = [
  { id: "economy", label: "Economy" },
  { id: "premium_economy", label: "Premium econ" },
  { id: "business", label: "Business" },
  { id: "first", label: "First" },
];

export const CARD_PRODUCTS: { id: CardProduct; label: string; cpp: string }[] = [
  { id: "venture_x", label: "Venture X", cpp: "1.25¢" },
  { id: "venture", label: "Venture", cpp: "1.0¢" },
];

/** Rough travel window — dates refine live seat search when a live L3 provider is keyed. */
export const TRAVEL_WINDOWS = [
  { id: "flexible", label: "Flexible (+/− 3 days)" },
  { id: "non_holiday", label: "Non-holiday" },
  { id: "next_60", label: "Next 60 days" },
  { id: "next_90", label: "Next 90 days" },
  { id: "peak_summer", label: "Peak summer (Jun–Aug)" },
  { id: "holidays", label: "Winter holidays (Dec)" },
] as const;

export type TravelWindowId = (typeof TRAVEL_WINDOWS)[number]["id"];

/** Map UI travel-window chips to ISO date bounds for the API. */
export function datesForTravelWindow(id: TravelWindowId): {
  start_date: string;
  end_date: string;
} {
  const today = new Date();
  const iso = (d: Date) => d.toISOString().slice(0, 10);

  if (id === "peak_summer") {
    const y = today.getMonth() >= 8 ? today.getFullYear() + 1 : today.getFullYear();
    return { start_date: `${y}-06-01`, end_date: `${y}-08-31` };
  }
  if (id === "holidays") {
    const y =
      today.getMonth() === 11 && today.getDate() > 25
        ? today.getFullYear() + 1
        : today.getFullYear();
    return { start_date: `${y}-12-15`, end_date: `${y}-12-31` };
  }

  const spans: Record<string, [number, number]> = {
    flexible: [0, 90],
    non_holiday: [14, 120],
    next_60: [0, 60],
    next_90: [0, 90],
  };
  const [a, b] = spans[id] ?? [0, 90];
  const start = new Date(today);
  const end = new Date(today);
  start.setDate(start.getDate() + a);
  end.setDate(end.getDate() + b);
  return { start_date: iso(start), end_date: iso(end) };
}

export function currencyLabel(id: CurrencyId): string {
  return CURRENCIES.find((c) => c.id === id)?.label ?? id;
}

export function currencyShort(id: CurrencyId): string {
  return CURRENCIES.find((c) => c.id === id)?.short ?? id;
}
