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
import { RaceList } from "@/components/race-list";
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
    // An empty database is the one state where the fetch page is the whole application,
    // so point at it rather than at a terminal command.
    return (
      <div className="space-y-4">
        <Empty message="No races loaded yet." />
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          <Link href="/fetch" className="underline underline-offset-2">
            Fetch one from the archive
          </Link>
          , or run <code>f1x ingest backfill</code> from a terminal.
        </p>
      </div>
    );
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

            <RaceList races={seasonRaces} />

          </section>
        ))}
    </div>
  );
}
