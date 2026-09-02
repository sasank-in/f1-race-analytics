/**
 * Driver ratings.
 *
 * The components are shown alongside the overall score rather than behind it. A single
 * number hides what a driver was actually good at, and the four components disagree
 * often enough that the disagreement is the interesting part — the quickest driver is
 * frequently not the best at managing tyres.
 */

import { api, type RatingsResponse } from "@/api/client";
import { Card, ErrorNote } from "@/components/ui";

export const revalidate = 60;

const COMPONENTS = [
  { key: "pace", label: "Pace", color: "var(--series-1)" },
  { key: "racecraft", label: "Racecraft", color: "var(--series-2)" },
  { key: "consistency", label: "Consistency", color: "var(--series-3)" },
  { key: "tyre_management", label: "Tyres", color: "var(--series-4)" },
] as const;

export default async function RatingsPage({
  searchParams,
}: {
  searchParams: Promise<{ season?: string }>;
}) {
  const params = await searchParams;

  let seasons: number[] = [];
  try {
    seasons = (await api.seasons()).map((s) => s.year);
  } catch {
    // Falls through to the error below when ratings also fail.
  }

  const season = Number(params.season) || seasons[0];
  if (!season) {
    return <ErrorNote detail="The API is unreachable. Start it with `f1x api serve`." />;
  }

  let ratings: RatingsResponse;
  try {
    ratings = await api.ratings(season);
  } catch (error) {
    return (
      <ErrorNote
        detail={error instanceof Error ? error.message : "Ratings unavailable"}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">
          {season} driver ratings
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          {ratings.note}
        </p>
        {seasons.length > 1 && (
          <nav className="mt-3 flex gap-2">
            {seasons.map((year) => (
              <a
                key={year}
                href={`/ratings?season=${year}`}
                className="rounded border px-2.5 py-1 text-xs"
                style={{
                  borderColor:
                    year === season ? "var(--text-primary)" : "var(--border)",
                  background:
                    year === season ? "var(--surface-2)" : "var(--surface-1)",
                }}
              >
                {year}
              </a>
            ))}
          </nav>
        )}
      </div>

      <Card
        title="Composite rating"
        subtitle="Pace, racecraft, consistency and tyre management, weighted 40/25/20/15"
        caveat="Each component is scaled across this season's field, so the numbers rank drivers within a season and carry no meaning across seasons."
      >
        <div className="space-y-3">
          {ratings.drivers.map((driver) => (
            <div key={driver.driver_number}>
              <div className="mb-1 flex items-baseline gap-3 text-xs">
                <span className="tnum w-5 text-right" style={{ color: "var(--text-muted)" }}>
                  {driver.rank}
                </span>
                <span className="tnum w-9 font-medium">#{driver.driver_number}</span>
                <span className="tnum w-12 font-semibold">
                  {driver.overall.toFixed(1)}
                </span>
                <span style={{ color: "var(--text-muted)" }}>
                  strongest: {driver.strongest.replace("_", " ")}
                </span>
                <span className="tnum ml-auto" style={{ color: "var(--text-muted)" }}>
                  {driver.n_races} races
                </span>
              </div>

              {/* Component bars. Each is labelled, so identity never rests on hue. */}
              <div className="ml-8 flex gap-1">
                {COMPONENTS.map((component) => {
                  const value = driver[component.key];
                  return (
                    <div key={component.key} className="flex-1">
                      <div
                        className="h-2 rounded-sm"
                        style={{
                          background: "var(--surface-2)",
                        }}
                      >
                        <div
                          className="h-2 rounded-sm"
                          style={{
                            width: `${value}%`,
                            background: component.color,
                          }}
                        />
                      </div>
                      <div
                        className="tnum mt-0.5 text-[10px]"
                        style={{ color: "var(--text-muted)" }}
                      >
                        {component.label} {value.toFixed(0)}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
