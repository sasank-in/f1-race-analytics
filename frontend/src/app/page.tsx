/**
 * Session index.
 *
 * The entry point: every analysis view is addressed by a session id, so this is how
 * a reader finds one. Grouped by season, newest first, because that is how anyone
 * looking for a specific race navigates.
 */

import Link from "next/link";

import { api, type Session } from "@/api/client";
import { Empty, ErrorNote } from "@/components/ui";

export const revalidate = 60;

export default async function SessionsPage() {
  let sessions: Session[];
  try {
    sessions = await api.sessions();
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

  if (sessions.length === 0) {
    return <Empty message="No sessions ingested yet. Run `f1x ingest backfill` first." />;
  }

  const bySeason = new Map<number, Session[]>();
  for (const session of sessions) {
    const list = bySeason.get(session.season_year) ?? [];
    list.push(session);
    bySeason.set(session.season_year, list);
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Sessions</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          {sessions.length} ingested across {bySeason.size} seasons.
        </p>
      </div>

      {[...bySeason.entries()]
        .sort((a, b) => b[0] - a[0])
        .map(([season, seasonSessions]) => (
          <section key={season}>
            <div className="mb-3 flex items-baseline gap-3">
              <h2 className="text-sm font-semibold">{season}</h2>
              <Link
                href={`/ratings?season=${season}`}
                className="text-xs underline underline-offset-2"
                style={{ color: "var(--text-secondary)" }}
              >
                driver ratings
              </Link>
            </div>

            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {seasonSessions.map((session) => (
                <Link
                  key={session.id}
                  href={`/sessions/${session.id}`}
                  className="rounded-lg border p-3 transition-colors hover:border-[var(--border-strong)]"
                  style={{
                    background: "var(--surface-1)",
                    borderColor: "var(--border)",
                  }}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium">
                      {session.event_name}
                    </span>
                    <span
                      className="tnum shrink-0 text-xs"
                      style={{ color: "var(--text-muted)" }}
                    >
                      R{session.round}
                    </span>
                  </div>
                  <div
                    className="mt-1 flex items-center gap-2 text-xs"
                    style={{ color: "var(--text-secondary)" }}
                  >
                    <span>{session.kind}</span>
                    {session.total_laps && (
                      <span className="tnum">{session.total_laps} laps</span>
                    )}
                    {/* Telemetry is not loaded for every session, and the corner
                        comparison needs it, so the state is worth showing here. */}
                    {session.telemetry_loaded && (
                      <span style={{ color: "var(--good)" }}>telemetry</span>
                    )}
                  </div>
                </Link>
              ))}
            </div>
          </section>
        ))}
    </div>
  );
}
