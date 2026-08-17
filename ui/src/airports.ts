export interface Airport {
  code: string;
  city: string;
  name: string;
}

/** Airports with award-chart / fare coverage in the vertical slice. */
export const AIRPORTS: Airport[] = [
  { code: "LAX", city: "Los Angeles", name: "Los Angeles Intl" },
  { code: "JFK", city: "New York", name: "John F Kennedy Intl" },
  { code: "SFO", city: "San Francisco", name: "San Francisco Intl" },
  { code: "SJC", city: "San Jose", name: "Norman Y Mineta San Jose Intl" },
  { code: "OAK", city: "Oakland", name: "Oakland Metro Oakland Intl" },
  { code: "SAN", city: "San Diego", name: "San Diego Intl" },
  { code: "SEA", city: "Seattle", name: "Seattle Tacoma Intl" },
  { code: "PDX", city: "Portland", name: "Portland Intl" },
  { code: "ORD", city: "Chicago", name: "O'Hare Intl" },
  { code: "EWR", city: "Newark", name: "Newark Liberty Intl" },
  { code: "IAD", city: "Washington", name: "Dulles Intl" },
  { code: "ONT", city: "Ontario", name: "Ontario Intl" },
  { code: "IST", city: "Istanbul", name: "Istanbul Airport" },
  { code: "LHR", city: "London", name: "Heathrow" },
  { code: "CDG", city: "Paris", name: "Charles de Gaulle" },
  { code: "FRA", city: "Frankfurt", name: "Frankfurt Main" },
  { code: "NRT", city: "Tokyo", name: "Narita Intl" },
  { code: "HND", city: "Tokyo", name: "Haneda" },
  { code: "ICN", city: "Seoul", name: "Incheon Intl" },
];

const BY_CODE = new Map(AIRPORTS.map((a) => [a.code, a]));

function scoreAirport(airport: Airport, query: string): number {
  const q = query.toUpperCase();
  const code = airport.code;
  const city = airport.city.toUpperCase();
  const name = airport.name.toUpperCase();

  if (code === q) return 1000;
  if (code.startsWith(q)) return 900 - (code.length - q.length);
  if (city.startsWith(q)) return 800;
  if (city.includes(q)) return 700;
  if (name.includes(q)) return 600;
  return 0;
}

export function searchAirports(query: string, limit = 6): Airport[] {
  const q = query.trim();
  if (!q) return AIRPORTS.slice(0, limit);

  return AIRPORTS.map((airport) => ({ airport, score: scoreAirport(airport, q) }))
    .filter(({ score }) => score > 0)
    .sort((a, b) => b.score - a.score || a.airport.code.localeCompare(b.airport.code))
    .slice(0, limit)
    .map(({ airport }) => airport);
}

export function resolveAirport(input: string): Airport | null {
  const q = input.trim().toUpperCase();
  if (!q) return null;

  const exact = BY_CODE.get(q);
  if (exact) return exact;

  const matches = searchAirports(q, 8);
  if (matches.length === 0) return null;

  const top = matches[0];
  if (top.code.startsWith(q)) return top;

  const topScore = scoreAirport(top, q);
  const tied = matches.filter((m) => scoreAirport(m, q) === topScore);
  if (tied.length === 1) return tied[0];

  return null;
}

export function isKnownAirport(input: string): boolean {
  return resolveAirport(input) !== null;
}

// REMOVED: ROUTES_WITH_FARES / routeHasFare.
//
// This was a hand-maintained copy of knowledge/fares.yaml living in the
// frontend, used to grey out demo buttons for routes with no cash fare. Two
// things were wrong with it. It drifted — the backend list grew and this one
// didn't, so real routes looked unsupported. And more fundamentally, a missing
// market fare no longer blocks anything: price paid comes from the fuel-charge
// matrix, so every route returns a full ranked answer and only the
// cents-per-point column goes missing. Gating the UI on fare coverage was the
// same mistake the pipeline used to make, mirrored one layer up.
