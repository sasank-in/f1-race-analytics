/**
 * Season view.
 *
 * The two questions that only have answers across a calendar: which circuits punish
 * tyres, and how each driver's pace moved through the year. A per-race page can show
 * neither — it can only ever say what happened once.
 */

import Link from "next/link";

import { api, formatLapTime, type SeasonPaceResponse, type SeasonProfileResponse } from "@/api/client";
import { SeasonPaceChart } from "@/components/season-charts";
import { Card, ErrorNote } from "@/components/ui";

export const revalidate = 60;

async function attempt<T>(fn: () => Promise<T>): Promise<T | { error: string }> {
  try {
    return await fn();
  } catch (error) {
    return { error: error instanceof Error ? error.message : "Request failed" };
  }
}

function isError<T>(value: T | { error: string }): value is { error: string } {
  return typeof value === "object" && value !== null && "error" in value;
}

export default async function SeasonPage({
  searchParams,
}: {
  searchParams: Promise<{ season?: string }>;
}) {
  const params = await searchParams;

  let seasons: number[] = [];
  try {
    seasons = (await api.seasons()).map((s) => s.year);
  } catch {
    // Reported below when both panels fail.
  }

  const season = Number(params.season) || seasons[0];
  if (!season) {
    return <ErrorNote detail="The API is unreachable. Start it with `f1x api serve`." />;
  }

  const [circuits, pace] = await Promise.all([
    attempt<SeasonProfileResponse>(() => api.circuits()),
    attempt<SeasonPaceResponse>(() => api.seasonPace(season)),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Season view</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          Patterns across the calendar, which no single race can show.
        </p>
        {seasons.length > 1 && (
          <nav className="mt-3 flex gap-2">
            {seasons.map((year) => (
              <a
                key={year}
                href={`/season?season=${year}`}
                className="rounded border px-2.5 py-1 text-xs"
                style={{
                  borderColor: year === season ? "var(--text-primary)" : "var(--border)",
                  background: year === season ? "var(--surface-2)" : "var(--surface-1)",
                }}
              >
                {year}
              </a>
            ))}
          </nav>
        )}
      </div>

      <Card
        title="Pace through the season"
        subtitle={`Gap to the quickest car, round by round, ${season}`}
        caveat="A line that falls is a car getting closer to the front. Missing points are races the driver did not complete."
      >
        {isError(pace) ? (
          <ErrorNote detail={pace.error} />
        ) : (
          <SeasonPaceChart data={pace} />
        )}
      </Card>

      <Card
        title="Circuits by tyre demand"
        subtitle="Median degradation across every race held there, all seasons pooled"
        caveat="Fitted from lap times, not measured. Only physical fits count — a negative slope means the stint was too short to support an estimate, not that the circuit is gentle."
      >
        {isError(circuits) ? (
          <ErrorNote detail={circuits.error} />
        ) : (
          <>
            {/* Column headers above the rows, not below: the same labels underneath
                read as a footnote and are found only after the reader has guessed. */}
            <div
              className="mb-2 flex gap-3 border-b pb-1.5 text-xs"
              style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
            >
              <span className="w-36">circuit</span>
              <span className="flex-1">tyre degradation</span>
              <span className="w-16 text-right">s/lap</span>
              <span className="w-20 text-right">stint life</span>
              <span className="w-20 text-right">lap time</span>
            </div>
            <div className="space-y-2">
              {(() => {
                const worst = Math.max(
                  ...circuits.circuits.map((c) => c.degradation_s_per_lap),
                  0.01,
                );
                return circuits.circuits.map((circuit) => (
                  <div key={circuit.circuit_key} className="flex items-center gap-3 text-xs">
                    <span className="w-36 shrink-0 truncate">
                      {circuit.circuit_key.replace(/-/g, " ")}
                    </span>
                    <div className="relative h-4 flex-1">
                      <div
                        className="h-4 rounded-r"
                        style={{
                          width: `${(circuit.degradation_s_per_lap / worst) * 100}%`,
                          background: "var(--series-2)",
                        }}
                      />
                    </div>
                    <span className="tnum w-16 text-right font-medium">
                      {circuit.degradation_s_per_lap.toFixed(3)}
                    </span>
                    <span
                      className="tnum w-20 text-right"
                      style={{ color: "var(--text-muted)" }}
                      title="Longest stint observed here"
                    >
                      {circuit.typical_stint_laps.toFixed(0)} laps
                    </span>
                    <span
                      className="tnum w-20 text-right"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {circuit.reference_lap_s
                        ? formatLapTime(circuit.reference_lap_s)
                        : "—"}
                    </span>
                  </div>
                ));
              })()}
            </div>
          </>
        )}
      </Card>

      <p className="text-xs" style={{ color: "var(--text-muted)" }}>
        <Link href="/" className="underline underline-offset-2">
          Back to races
        </Link>
      </p>
    </div>
  );
}
