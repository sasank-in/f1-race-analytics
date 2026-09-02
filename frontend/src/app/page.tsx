/**
 * Race index.
 *
 * A list of 44 identically-shaped rows gives a reader no reason to pick one. Each race
 * therefore carries a line saying what happened, and the races where the quickest car
 * did *not* win are marked — those are where the interesting analysis lives, and they
 * are the thing a results table can never point you at.
 */

import Link from "next/link";

import { api, type SessionSummary } from "@/api/client";
import { Empty, ErrorNote } from "@/components/ui";

export const revalidate = 60;

export default async function RacesPage() {
  let races: SessionSummary[];
  try {
    races = await api.summaries();
  } catch (error) {
    return (
      <ErrorNote
        detail={
          error instanceof Error
            ? error.message
            : "The API is unreachable. Start it with `f1x api serve`."
        }
      />
    );
  }

  if (races.length === 0) {
    return <Empty message="No races ingested yet. Run `f1x ingest backfill` first." />;
  }

  const bySeason = new Map<number, SessionSummary[]>();
  for (const race of races) {
    const list = bySeason.get(race.season_year) ?? [];
    list.push(race);
    bySeason.set(race.season_year, list);
  }

  const mismatches = races.filter((r) => r.pace_winner_mismatch).length;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Races</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          {races.length} across {bySeason.size} seasons.{" "}
          <span style={{ color: "var(--text-primary)" }}>
            In {mismatches} of them the quickest car did not win.
          </span>
        </p>
      </div>

      {[...bySeason.entries()]
        .sort((a, b) => b[0] - a[0])
        .map(([season, seasonRaces]) => (
          <section key={season}>
            <div className="mb-3 flex items-baseline gap-3">
              <h2 className="text-sm font-semibold">{season}</h2>
              <Link
                href={`/season?season=${season}`}
                className="text-xs underline underline-offset-2"
                style={{ color: "var(--text-secondary)" }}
              >
                season view
              </Link>
              <Link
                href={`/teammates?season=${season}`}
                className="text-xs underline underline-offset-2"
                style={{ color: "var(--text-secondary)" }}
              >
                teammates
              </Link>
              <Link
                href={`/ratings?season=${season}`}
                className="text-xs underline underline-offset-2"
                style={{ color: "var(--text-secondary)" }}
              >
                ratings
              </Link>
            </div>

            <div
              className="overflow-hidden rounded-lg border"
              style={{ borderColor: "var(--border)" }}
            >
              {seasonRaces.map((race, index) => (
                <Link
                  key={race.session_id}
                  href={`/sessions/${race.session_id}`}
                  className="flex items-baseline gap-3 px-4 py-2.5 transition-colors hover:bg-[var(--surface-2)]"
                  style={{
                    background: "var(--surface-1)",
                    borderTop: index > 0 ? "1px solid var(--border)" : undefined,
                  }}
                >
                  <span
                    className="tnum w-6 shrink-0 text-xs"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {race.round}
                  </span>
                  <span className="w-44 shrink-0 truncate text-sm font-medium">
                    {race.event_name.replace(" Grand Prix", "")}
                  </span>

                  {/* The headline is the reason to click. */}
                  <span
                    className="flex-1 truncate text-xs"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    {race.headline}
                  </span>

                  {race.pace_winner_mismatch && (
                    <span
                      className="shrink-0 rounded px-1.5 py-0.5 text-[10px]"
                      style={{ background: "var(--surface-2)", color: "var(--series-2)" }}
                      title="The quickest car did not win — worth a look"
                    >
                      upset
                    </span>
                  )}
                  {race.telemetry_loaded && (
                    <span
                      className="shrink-0 text-[10px]"
                      style={{ color: "var(--good)" }}
                      title="Telemetry loaded: corner and track-map analysis available"
                    >
                      telemetry
                    </span>
                  )}
                </Link>
              ))}
            </div>
          </section>
        ))}
    </div>
  );
}
