/**
 * Teammate comparison.
 *
 * The one comparison that removes the car. Presented as a diverging bar — each pairing
 * on its own row, the gap measured from a centre line — because the question is
 * directional: which of the two, and by how much.
 */

import { api, type TeammatesResponse } from "@/api/client";
import { Card, ErrorNote } from "@/components/ui";

export const revalidate = 60;

export default async function TeammatesPage({
  searchParams,
}: {
  searchParams: Promise<{ season?: string }>;
}) {
  const params = await searchParams;

  let seasons: number[] = [];
  try {
    seasons = (await api.seasons()).map((s) => s.year);
  } catch {
    // Handled by the ratings error below when the API is unreachable.
  }

  const season = Number(params.season) || seasons[0];
  if (!season) {
    return <ErrorNote detail="The API is unreachable. Start it with `f1x api serve`." />;
  }

  let data: TeammatesResponse;
  try {
    data = await api.teammates(season);
  } catch (error) {
    return (
      <ErrorNote
        detail={error instanceof Error ? error.message : "Comparison unavailable"}
      />
    );
  }

  const pairings = data.pairings.filter((p) => p.is_reliable);
  const widest = Math.max(...pairings.map((p) => p.margin_s), 0.1);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">
          {season} teammate comparison
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          {data.note}
        </p>
        {seasons.length > 1 && (
          <nav className="mt-3 flex gap-2">
            {seasons.map((year) => (
              <a
                key={year}
                href={`/teammates?season=${year}`}
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
        title="Head to head"
        subtitle="Median pace gap between drivers who shared a car, over the sessions they both finished"
        caveat="A marked pairing led at least 70% of shared weekends — consistently ahead, not merely ahead on average."
      >
        <div className="space-y-3">
          {pairings.map((pairing) => {
            const aFaster = pairing.faster_driver === pairing.driver_a;
            const width = (pairing.margin_s / widest) * 46;
            const total = pairing.sessions_a_ahead + pairing.sessions_b_ahead;
            return (
              <div key={`${pairing.team_key}-${pairing.driver_a}-${pairing.driver_b}`}>
                <div className="mb-1 flex items-baseline gap-2 text-xs">
                  <span className="w-24 truncate" style={{ color: "var(--text-secondary)" }}>
                    {pairing.team_key ?? "—"}
                  </span>
                  <span className="tnum font-medium">
                    #{pairing.faster_driver}
                  </span>
                  <span style={{ color: "var(--text-muted)" }}>by</span>
                  <span className="tnum font-semibold">
                    {pairing.margin_s.toFixed(3)}s
                  </span>
                  {pairing.is_decisive && (
                    <span
                      className="rounded px-1.5 py-0.5 text-[10px]"
                      style={{ background: "var(--surface-2)", color: "var(--good)" }}
                    >
                      consistent
                    </span>
                  )}
                  <span className="tnum ml-auto" style={{ color: "var(--text-muted)" }}>
                    {pairing.sessions_a_ahead}–{pairing.sessions_b_ahead} over{" "}
                    {pairing.n_sessions}
                  </span>
                </div>

                {/* Diverging from the centre: driver A left, driver B right. */}
                <div className="flex items-center gap-2">
                  <span className="tnum w-8 text-right text-xs">
                    #{pairing.driver_a}
                  </span>
                  <div className="relative h-4 flex-1">
                    <div
                      className="absolute inset-y-0"
                      style={{ left: "50%", width: 1, background: "var(--border-strong)" }}
                    />
                    <div
                      className="absolute h-4 rounded-sm"
                      style={{
                        left: aFaster ? `${50 - width}%` : "50%",
                        width: `${width}%`,
                        background: aFaster ? "var(--series-1)" : "var(--series-2)",
                      }}
                    />
                  </div>
                  <span className="tnum w-8 text-xs">#{pairing.driver_b}</span>
                </div>

                {/* Head-to-head split, which is the part that distinguishes a settled
                    result from a lucky average. */}
                <div className="ml-10 mr-10 mt-1 flex h-1 overflow-hidden rounded-sm">
                  <div
                    style={{
                      width: `${total ? (pairing.sessions_a_ahead / total) * 100 : 50}%`,
                      background: "var(--series-1)",
                      opacity: 0.5,
                    }}
                  />
                  <div
                    style={{
                      width: `${total ? (pairing.sessions_b_ahead / total) * 100 : 50}%`,
                      background: "var(--series-2)",
                      opacity: 0.5,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>

        {data.pairings.length > pairings.length && (
          <p
            className="mt-4 border-t pt-3 text-xs"
            style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
          >
            {data.pairings.length - pairings.length} pairing
            {data.pairings.length - pairings.length === 1 ? " is" : "s are"} hidden:
            too few shared sessions, or a gap large enough to be a broken car rather
            than a driver difference.
          </p>
        )}
      </Card>
    </div>
  );
}
