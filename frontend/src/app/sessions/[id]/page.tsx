/**
 * Session dashboard.
 *
 * Everything the engine knows about one race, in the order a reader needs it: who was
 * quick, how the tyres behaved, what the strategy was worth, and how confident the
 * simulation is about that.
 *
 * Sections fetch independently. A session without telemetry still shows pace and
 * strategy rather than failing whole, which matters because telemetry is loaded
 * selectively.
 */

import Link from "next/link";

import { api, formatLapTime, type Session } from "@/api/client";
import { DegradationChart, PaceChart } from "@/components/charts";
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

export default async function SessionPage({
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
      <ErrorNote
        detail={error instanceof Error ? error.message : "Session not found"}
      />
    );
  }

  const [pace, degradation, strategy, simulation] = await Promise.all([
    attempt(() => api.pace(sessionId)),
    attempt(() => api.degradation(sessionId)),
    attempt(() => api.strategy(sessionId)),
    attempt(() => api.simulate(sessionId, 2000)),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/"
          className="text-xs underline underline-offset-2"
          style={{ color: "var(--text-secondary)" }}
        >
          ← all sessions
        </Link>
        <h1 className="mt-2 text-lg font-semibold tracking-tight">
          {session.event_name}
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          {session.season_year} · round {session.round} · {session.kind}
          {session.total_laps ? ` · ${session.total_laps} laps` : ""}
          {!isError(pace) ? ` · engine ${pace.meta.engine_version}` : ""}
        </p>

        {/* The deeper views. Telemetry only exists for sessions ingested with it,
            so the link says so rather than leading to an error. */}
        <nav className="mt-3 flex gap-2">
          <Link
            href={`/sessions/${sessionId}/strategy`}
            className="rounded border px-2.5 py-1 text-xs"
            style={{ borderColor: "var(--border-strong)", background: "var(--surface-1)" }}
          >
            Strategy &amp; undercuts →
          </Link>
          {session.telemetry_loaded ? (
            <Link
              href={`/sessions/${sessionId}/telemetry`}
              className="rounded border px-2.5 py-1 text-xs"
              style={{ borderColor: "var(--border-strong)", background: "var(--surface-1)" }}
            >
              Telemetry comparison →
            </Link>
          ) : (
            <span
              className="rounded border border-dashed px-2.5 py-1 text-xs"
              style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
              title="Re-ingest this session with telemetry to enable the comparison"
            >
              Telemetry not loaded
            </span>
          )}
        </nav>
      </div>

      <Card
        title="Race pace"
        subtitle="20th percentile of clean fuel-corrected laps, ranked"
        caveat="Not the fastest lap, which rewards the best single opportunity, and not the mean, which rewards avoiding traffic."
      >
        {isError(pace) ? (
          <ErrorNote detail={pace.error} />
        ) : pace.drivers.length === 0 ? (
          <Empty message="No pace rankings for this session." />
        ) : (
          <PaceChart data={pace} />
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card
          title="Tyre degradation"
          subtitle="Median slope per compound, with interquartile spread"
          caveat="Fitted from lap times, not measured. The spread is the honest width of the estimate."
        >
          {isError(degradation) ? (
            <ErrorNote detail={degradation.error} />
          ) : degradation.compounds.length === 0 ? (
            <Empty message="No degradation curves for this session." />
          ) : (
            <>
              <DegradationChart data={degradation} />
              {/* A negative fit means the stint was too short to support an estimate.
                  Surfacing the count keeps that visible rather than hidden. */}
              {(() => {
                const unphysical = degradation.stints.filter(
                  (s) => s.is_physical === false,
                ).length;
                return unphysical > 0 ? (
                  <p
                    className="mt-4 border-t pt-3 text-xs"
                    style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
                  >
                    {unphysical} of {degradation.stints.length} stint fits returned a
                    negative slope. Those runs were too short to support an estimate;
                    they are excluded downstream rather than treated as tyres improving
                    with age.
                  </p>
                ) : null;
              })()}
            </>
          )}
        </Card>

        <Card
          title="Strategy"
          subtitle="Pit loss and ranked stop counts"
          caveat="Pit loss is what the in-lap and out-lap add beyond two normal laps, not pit-lane transit time."
        >
          {isError(strategy) ? (
            <ErrorNote detail={strategy.error} />
          ) : (
            <>
              <div className="mb-5 grid grid-cols-3 gap-4">
                <Stat
                  label="Cost of a stop"
                  value={strategy.pit_loss.net_loss_s.toFixed(1)}
                  unit="s"
                  hint={`from ${strategy.pit_loss.n_stops} clean stops`}
                />
                <Stat
                  label="Pit-lane transit"
                  value={strategy.pit_loss.pit_window_s.toFixed(1)}
                  unit="s"
                />
                <Stat
                  label="Reference lap"
                  value={formatLapTime(strategy.pit_loss.reference_lap_s)}
                />
              </div>

              <table className="w-full text-xs">
                <thead>
                  <tr style={{ color: "var(--text-muted)" }}>
                    <th className="pb-2 text-left font-normal">stops</th>
                    <th className="pb-2 text-left font-normal">stints</th>
                    <th className="pb-2 text-right font-normal">
                      lost to tyres
                    </th>
                    <th className="pb-2 text-right font-normal">lost to stops</th>
                    <th className="pb-2 text-right font-normal">total</th>
                  </tr>
                </thead>
                <tbody>
                  {strategy.options.map((option, index) => (
                    <tr
                      key={option.n_stops}
                      style={{
                        fontWeight: index === 0 ? 600 : 400,
                        color:
                          index === 0 ? "var(--text-primary)" : "var(--text-secondary)",
                      }}
                    >
                      <td className="tnum py-1">{option.n_stops}</td>
                      <td className="tnum py-1">{option.stint_lengths.join("-")}</td>
                      <td className="tnum py-1 text-right">
                        {option.degradation_cost_s.toFixed(0)}s
                      </td>
                      <td className="tnum py-1 text-right">
                        {option.pit_cost_s.toFixed(0)}s
                      </td>
                      <td className="tnum py-1 text-right">
                        {option.total_cost_s.toFixed(0)}s
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </Card>
      </div>

      <Card
        title="Race simulation"
        subtitle="Each strategy run thousands of times with safety cars and lap-time noise resampled"
        caveat="A strategy that wins 51% of the time is a coin toss; one that wins 90% is a decision."
      >
        {isError(simulation) ? (
          <ErrorNote detail={simulation.error} />
        ) : (
          <>
            <div className="mb-4 flex flex-wrap gap-6">
              <Stat
                label="Iterations"
                value={simulation.iterations.toLocaleString()}
              />
              <Stat
                label="Safety car"
                value={`${Math.round(simulation.safety_car_rate * 100)}`}
                unit="% of runs"
              />
              <Stat
                label="Verdict"
                value={simulation.is_decisive ? "Clear" : "Too close"}
                hint={
                  simulation.is_decisive
                    ? "the leading strategy wins often enough to call"
                    : "no strategy wins often enough to be confident"
                }
              />
            </div>

            <div className="space-y-2">
              {simulation.strategies.map((strategy) => (
                <div key={strategy.n_stops} className="flex items-center gap-3 text-xs">
                  <span className="w-14 font-medium">{strategy.n_stops}-stop</span>
                  <div className="relative h-4 flex-1">
                    <div
                      className="h-4 rounded-r"
                      style={{
                        width: `${Math.max(strategy.win_rate * 100, 0.5)}%`,
                        background: "var(--series-1)",
                      }}
                    />
                  </div>
                  <span className="tnum w-12 text-right font-medium">
                    {(strategy.win_rate * 100).toFixed(1)}%
                  </span>
                  <span
                    className="tnum w-24 text-right"
                    style={{ color: "var(--text-muted)" }}
                  >
                    ±{strategy.spread_s.toFixed(1)}s
                  </span>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>
              Bars show how often each strategy produced the fastest race; the trailing
              figure is the width of the middle 90 % of outcomes.
            </p>
          </>
        )}
      </Card>

      {!isError(degradation) && degradation.stints.length > 0 && (
        <Card
          title="Stint fits"
          subtitle="Every fitted stint, including the ones that failed"
        >
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ color: "var(--text-muted)" }}>
                  <th className="pb-2 text-left font-normal">car</th>
                  <th className="pb-2 text-left font-normal">stint</th>
                  <th className="pb-2 text-left font-normal">compound</th>
                  <th className="pb-2 text-right font-normal">laps</th>
                  <th className="pb-2 text-right font-normal">pace</th>
                  <th className="pb-2 text-right font-normal">deg s/lap</th>
                  <th className="pb-2 text-right font-normal">r²</th>
                </tr>
              </thead>
              <tbody>
                {degradation.stints.slice(0, 40).map((stint) => (
                  <tr
                    key={`${stint.driver_number}-${stint.stint}`}
                    style={{
                      color:
                        stint.is_physical === false
                          ? "var(--text-muted)"
                          : "var(--text-secondary)",
                    }}
                  >
                    <td className="tnum py-1">#{stint.driver_number}</td>
                    <td className="tnum py-1">{stint.stint}</td>
                    <td className="py-1">
                      <CompoundTag compound={stint.compound} />
                    </td>
                    <td className="tnum py-1 text-right">{stint.n_laps}</td>
                    <td className="tnum py-1 text-right">
                      {formatLapTime(stint.pace_s)}
                    </td>
                    <td
                      className="tnum py-1 text-right"
                      style={{
                        color:
                          stint.is_physical === false
                            ? "var(--warning)"
                            : "var(--text-primary)",
                      }}
                      title={
                        stint.is_physical === false
                          ? "Negative slope: the stint was too short to support an estimate"
                          : undefined
                      }
                    >
                      {stint.degradation_s_per_lap >= 0 ? "+" : ""}
                      {stint.degradation_s_per_lap.toFixed(3)}
                    </td>
                    <td className="tnum py-1 text-right">
                      {stint.r_squared?.toFixed(2) ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
