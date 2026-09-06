/**
 * Fetch a race from the archive.
 *
 * Every other page reads what has already been ingested. This one shows the published
 * calendar — including seasons with nothing stored locally — so a race can be pulled in
 * without leaving the application for a terminal.
 *
 * A client component throughout: a fetch takes minutes, and the page has to keep
 * polling and re-rendering while it runs.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

import { api, type FetchJob, type ScheduledRace } from "@/api/client";
import { Card, ErrorNote } from "@/components/ui";

/** How often to poll a running job. Fast enough to feel live, slow enough to be cheap. */
const POLL_MS = 2000;

/** Seasons offered. Lap timing begins in 2018, and the engine needs laps. */
const FIRST_SEASON = 2018;

function seasonChoices(): number[] {
  const current = new Date().getUTCFullYear();
  const years: number[] = [];
  for (let year = current; year >= FIRST_SEASON; year--) years.push(year);
  return years;
}

/**
 * Open on the previous season rather than the current one.
 *
 * A season in progress is mostly races that have not happened, so landing there shows
 * a calendar of "not yet run" and little to act on. The completed season before it is
 * the one with something to fetch, and the picker is right there for the current year.
 */
function defaultSeason(): number {
  return new Date().getUTCFullYear() - 1;
}

export default function FetchPage() {
  const [season, setSeason] = useState<number>(defaultSeason());
  const [races, setRaces] = useState<ScheduledRace[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [telemetry, setTelemetry] = useState(true);
  const [job, setJob] = useState<FetchJob | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);

  // Held in a ref so the poll effect can clear a timer it did not create.
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadSchedule = useCallback(async (year: number) => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.schedule(year);
      setRaces(response.races);
    } catch (err) {
      setRaces(null);
      setError(
        err instanceof Error
          ? err.message
          : "Could not reach the archive. Check your connection.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSchedule(season);
  }, [season, loadSchedule]);

  // Poll while a job is running. Refreshing the schedule on completion is what flips
  // the row from "available" to "ingested" and reveals the link to the race.
  useEffect(() => {
    if (!job || job.state === "complete" || job.state === "failed") return;

    const tick = async () => {
      try {
        const next = await api.fetchJob(job.id);
        setJob(next);
        if (next.state === "complete") void loadSchedule(season);
        if (next.state !== "complete" && next.state !== "failed") {
          pollRef.current = setTimeout(tick, POLL_MS);
        }
      } catch (err) {
        setJobError(err instanceof Error ? err.message : "Lost track of the fetch");
      }
    };

    pollRef.current = setTimeout(tick, POLL_MS);
    return () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [job, season, loadSchedule]);

  const start = async (race: ScheduledRace) => {
    setJobError(null);
    try {
      setJob(await api.startFetch(race.season, race.round, "R", telemetry));
    } catch (err) {
      setJobError(err instanceof Error ? err.message : "Could not start the fetch");
    }
  };

  const running = job !== null && job.state !== "complete" && job.state !== "failed";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Fetch a race</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          The calendar below comes from the Live Timing archive, not this database — so
          it lists races whether or not they have been loaded.
        </p>
      </div>

      <Card
        title="Season"
        subtitle="Lap timing begins in 2018, and the analysis needs laps"
      >
        <div className="flex flex-wrap gap-2">
          {seasonChoices().map((year) => (
            <button
              key={year}
              onClick={() => setSeason(year)}
              className="rounded border px-2.5 py-1 text-xs"
              style={{
                borderColor: year === season ? "var(--text-primary)" : "var(--border)",
                background: year === season ? "var(--surface-2)" : "var(--surface-1)",
                color: "var(--text-primary)",
              }}
            >
              {year}
            </button>
          ))}
        </div>

        <label className="mt-4 flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={telemetry}
            onChange={(event) => setTelemetry(event.target.checked)}
          />
          <span>
            Include telemetry
            <span style={{ color: "var(--text-muted)" }}>
              {" "}
              — roughly triples the time, and is required for track maps and corner
              analysis
            </span>
          </span>
        </label>
      </Card>

      {job && (
        <Card
          title="Fetch in progress"
          subtitle={`${job.year} round ${job.round} ${job.kind}`}
          caveat="Ingest, transform and analyse all run before a race becomes viewable. Leaving this page does not cancel the fetch."
        >
          <FetchProgress job={job} />
        </Card>
      )}

      {jobError && <ErrorNote detail={jobError} />}

      <Card
        title={`${season} calendar`}
        subtitle={
          races
            ? `${races.filter((r) => r.is_ingested).length} of ${races.length} rounds loaded`
            : "Loading from the archive"
        }
      >
        {error ? (
          <ErrorNote detail={error} />
        ) : loading && !races ? (
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            Reading the calendar…
          </p>
        ) : (
          <>
            {/* Column headers above the rows, not below. */}
            <div
              className="mb-2 flex items-center gap-3 border-b pb-1.5 text-xs"
              style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
            >
              <span className="w-8 text-right">rd</span>
              <span className="flex-1">race</span>
              <span className="w-24">date</span>
              <span className="w-28 text-right">status</span>
            </div>

            <div className="space-y-1">
              {(races ?? []).map((race) => (
                <ScheduleRow
                  key={race.round}
                  race={race}
                  disabled={running}
                  onFetch={() => start(race)}
                />
              ))}
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

function FetchProgress({ job }: { job: FetchJob }) {
  const failed = job.state === "failed";
  const complete = job.state === "complete";

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium">{job.detail}</span>
        <span className="tnum" style={{ color: "var(--text-muted)" }}>
          {Math.round(job.progress * 100)}%
        </span>
      </div>

      <div
        className="h-2 w-full overflow-hidden rounded"
        style={{ background: "var(--surface-2)" }}
      >
        <div
          className="h-2 rounded transition-all duration-500"
          style={{
            width: `${Math.max(job.progress * 100, 2)}%`,
            background: failed ? "var(--critical)" : complete ? "var(--good)" : "var(--series-1)",
          }}
        />
      </div>

      {job.laps > 0 && (
        <p className="tnum text-xs" style={{ color: "var(--text-muted)" }}>
          {job.laps.toLocaleString()} laps
          {job.telemetry_samples > 0 &&
            `, ${job.telemetry_samples.toLocaleString()} telemetry samples`}
        </p>
      )}

      {(job.warnings?.length ?? 0) > 0 && (
        <ul className="space-y-0.5 text-xs" style={{ color: "var(--warning)" }}>
          {job.warnings?.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}

      {failed && job.error && <ErrorNote detail={job.error} />}

      {complete && job.session_id && (
        <Link
          href={`/sessions/${job.session_id}`}
          className="inline-block rounded border px-3 py-1.5 text-xs font-medium"
          style={{ borderColor: "var(--text-primary)", background: "var(--surface-2)" }}
        >
          Open the analysis →
        </Link>
      )}
    </div>
  );
}

function ScheduleRow({
  race,
  disabled,
  onFetch,
}: {
  race: ScheduledRace;
  disabled: boolean;
  onFetch: () => void;
}) {
  return (
    <div className="flex items-center gap-3 text-xs">
      <span className="tnum w-8 shrink-0 text-right" style={{ color: "var(--text-muted)" }}>
        {race.round}
      </span>
      <span className="flex-1 truncate">
        {race.name}
        {race.country && (
          <span style={{ color: "var(--text-muted)" }}> · {race.country}</span>
        )}
      </span>
      <span className="tnum w-24 shrink-0" style={{ color: "var(--text-muted)" }}>
        {race.event_date ?? "—"}
      </span>

      <span className="flex w-28 shrink-0 justify-end">
        {race.is_ingested && race.session_id ? (
          <Link
            href={`/sessions/${race.session_id}`}
            className="underline underline-offset-2"
            style={{ color: "var(--good)" }}
          >
            analyse →
          </Link>
        ) : !race.has_run ? (
          <span style={{ color: "var(--text-muted)" }}>not yet run</span>
        ) : (
          <button
            onClick={onFetch}
            disabled={disabled}
            className="rounded border px-2 py-0.5 disabled:opacity-40"
            style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}
            title={disabled ? "A fetch is already running" : "Fetch this race"}
          >
            fetch
          </button>
        )}
      </span>
    </div>
  );
}
