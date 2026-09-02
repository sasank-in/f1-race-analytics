/**
 * Strategy detail.
 *
 * The stint timeline and undercut scan, which are the two things a strategist actually
 * looks at and neither of which fits on the session summary.
 */

import Link from "next/link";

import { api, type Session } from "@/api/client";
import { StintTimeline, UndercutChart } from "@/components/strategy-charts";
import { Card, CompoundTag, Empty, ErrorNote, Stat } from "@/components/ui";

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

export default async function StrategyPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const sessionId = Number(id);

  let session: Session;
  try {
    session = await api.session(sessionId);
  } catch (error) {
    return (
      <ErrorNote detail={error instanceof Error ? error.message : "Session not found"} />
    );
  }

  const [stints, undercut] = await Promise.all([
    attempt(() => api.stints(sessionId)),
    attempt(() => api.undercut(sessionId)),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={`/sessions/${sessionId}`}
          className="text-xs underline underline-offset-2"
          style={{ color: "var(--text-secondary)" }}
        >
          ← session
        </Link>
        <h1 className="mt-2 text-lg font-semibold tracking-tight">
          Strategy — {session.event_name}
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          {session.season_year} · round {session.round} · {session.total_laps} laps
        </p>
      </div>

      <Card
        title="Stint timeline"
        subtitle="Every driver's race, coloured by compound"
        caveat="Read down a column to see who pitted together; across a row to see one driver's whole strategy."
      >
        {isError(stints) ? (
          <ErrorNote detail={stints.error} />
        ) : (
          <>
            <div className="mb-4 flex flex-wrap gap-4">
              {["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET"]
                .filter((c) => stints.stints.some((s) => s.compound === c))
                .map((compound) => (
                  <CompoundTag key={compound} compound={compound} />
                ))}
            </div>
            <StintTimeline stints={stints.stints} totalLaps={stints.total_laps} />
          </>
        )}
      </Card>

      <Card
        title="Undercut opportunities"
        subtitle="Every lap a driver sat within three seconds of the car ahead"
        caveat="Whether pitting would gain the place, given the rival's tyre age and the cost of a stop. A model, not a record of what teams did."
      >
        {isError(undercut) ? (
          <ErrorNote detail={undercut.error} />
        ) : undercut.windows.length === 0 ? (
          <Empty message="No cars ran close enough to model an undercut." />
        ) : (
          <>
            <div className="mb-5 flex flex-wrap gap-6">
              <Stat
                label="Opportunities"
                value={undercut.windows.length.toLocaleString()}
                hint="laps within striking distance"
              />
              <Stat
                label="Would have worked"
                value={undercut.windows
                  .filter((w) => w.verdict === "undercut")
                  .length.toLocaleString()}
              />
              <Stat
                label="Cost of a stop"
                value={undercut.net_pit_loss_s.toFixed(1)}
                unit="s"
              />
              <Stat
                label="Degradation used"
                value={undercut.degradation_s_per_lap.toFixed(3)}
                unit="s/lap"
              />
            </div>

            <UndercutChart
              windows={undercut.windows}
              totalLaps={session.total_laps}
            />

            <div className="mt-6">
              <h3 className="mb-2 text-xs font-semibold">
                Closest calls
              </h3>
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ color: "var(--text-muted)" }}>
                    <th className="pb-1 text-left font-normal">lap</th>
                    <th className="pb-1 text-left font-normal">chasing</th>
                    <th className="pb-1 text-right font-normal">gap</th>
                    <th className="pb-1 text-right font-normal">gain</th>
                    <th className="pb-1 text-right font-normal">margin</th>
                    <th className="pb-1 text-right font-normal">verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {/* The marginal ones are the interesting ones: a call that is
                      clearly on or clearly off needs no analysis. */}
                  {undercut.windows
                    .filter((w) => w.verdict === "marginal")
                    .slice(0, 10)
                    .map((w, i) => (
                      <tr key={`${w.lap_number}-${w.attacker}-${i}`}
                        style={{ color: "var(--text-secondary)" }}>
                        <td className="tnum py-1">{w.lap_number}</td>
                        <td className="tnum py-1">
                          #{w.attacker} → #{w.defender}
                        </td>
                        <td className="tnum py-1 text-right">{w.gap_s.toFixed(2)}s</td>
                        <td className="tnum py-1 text-right">
                          {w.total_gain_s.toFixed(2)}s
                        </td>
                        <td className="tnum py-1 text-right">
                          {w.margin_s >= 0 ? "+" : ""}
                          {w.margin_s.toFixed(2)}s
                        </td>
                        <td
                          className="py-1 text-right"
                          style={{ color: "var(--series-4)" }}
                        >
                          {w.verdict}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
              {undercut.windows.filter((w) => w.verdict === "marginal").length === 0 && (
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                  No marginal calls — every opportunity was clearly on or clearly off.
                </p>
              )}
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
