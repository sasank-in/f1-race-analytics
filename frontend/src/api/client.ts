/**
 * Typed API client.
 *
 * Response types come from `schema.ts`, generated from the backend's OpenAPI document.
 * That means a field renamed in a Pydantic model surfaces here as a compile error
 * rather than as an undefined value in a chart at runtime.
 */

import type { components } from "./schema";

type Schemas = components["schemas"];

export type Season = Schemas["SeasonOut"];
export type Event = Schemas["EventOut"];
export type Session = Schemas["SessionOut"];
export type Lap = Schemas["LapOut"];
export type PaceResponse = Schemas["PaceResponse"];
export type DegradationResponse = Schemas["DegradationResponse"];
export type StrategyResponse = Schemas["StrategyResponse"];
export type SimulationResponse = Schemas["SimulationResponse"];
export type RatingsResponse = Schemas["RatingsResponse"];
export type TelemetryCompareResponse = Schemas["TelemetryCompareResponse"];
export type DriverRating = Schemas["DriverRatingOut"];
export type StintFit = Schemas["StintFitOut"];
export type UndercutResponse = Schemas["UndercutResponse"];
export type UndercutWindow = Schemas["UndercutWindowOut"];
export type StintTimelineResponse = Schemas["StintTimelineResponse"];
export type Stint = Schemas["StintOut"];
export type Corner = Schemas["CornerDeltaOut"];

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    /** The backend's own message, which says what to run when data is missing. */
    readonly detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}/api/v1${path}`, {
    // Analysis output only changes when the engine version does, so the browser may
    // hold it briefly. The backend's Redis cache does the real work.
    next: { revalidate: 60 },
  });

  if (!response.ok) {
    // The API returns an actionable detail — "run f1x analyse first" — so surface it
    // rather than replacing it with a generic failure message.
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // Non-JSON error body; the status-based message stands.
    }
    throw new ApiError(response.status, detail);
  }

  return response.json() as Promise<T>;
}

export const api = {
  seasons: () => get<Season[]>("/seasons"),
  events: (season?: number) =>
    get<Event[]>(season ? `/events?season=${season}` : "/events"),
  sessions: (season?: number) =>
    get<Session[]>(season ? `/sessions?season=${season}` : "/sessions"),
  session: (id: number) => get<Session>(`/sessions/${id}`),

  laps: (id: number, representativeOnly = false) =>
    get<Lap[]>(
      `/analysis/laps/${id}${representativeOnly ? "?representative_only=true" : ""}`,
    ),
  pace: (id: number) => get<PaceResponse>(`/analysis/pace/${id}`),
  degradation: (id: number) => get<DegradationResponse>(`/analysis/degradation/${id}`),

  strategy: (id: number) => get<StrategyResponse>(`/strategy/${id}`),
  undercut: (id: number, maxGap = 3) =>
    get<UndercutResponse>(`/undercut/${id}?max_gap_s=${maxGap}`),
  stints: (id: number) => get<StintTimelineResponse>(`/stints/${id}`),
  simulate: (id: number, iterations = 2000) =>
    get<SimulationResponse>(`/simulate/${id}?iterations=${iterations}`),
  ratings: (season: number) => get<RatingsResponse>(`/ratings/${season}`),
  telemetry: (id: number, a: string, lapA: number, b: string, lapB: number) =>
    get<TelemetryCompareResponse>(
      `/telemetry/compare/${id}?driver_a=${a}&lap_a=${lapA}&driver_b=${b}&lap_b=${lapB}`,
    ),
};

/** Seconds to a lap-time string: 95.522 becomes 1:35.522. */
export function formatLapTime(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return minutes > 0
    ? `${minutes}:${rest.toFixed(3).padStart(6, "0")}`
    : rest.toFixed(3);
}

/** A gap, always signed, so "+0.110" reads unambiguously against "0.000". */
export function formatGap(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  if (Math.abs(seconds) < 0.0005) return "—";
  return `${seconds > 0 ? "+" : ""}${seconds.toFixed(3)}`;
}
