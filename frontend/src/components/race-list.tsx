/**
 * Race list for one season.
 *
 * A season is 22-24 rounds, and an unbroken list of that many identical rows is a wall
 * to scroll rather than something to choose from. Three things fix it without hiding
 * anything: a filter box, a toggle for the races where pace and result disagreed, and
 * a collapse to the first ten with the rest one click away.
 *
 * Collapsing rather than paginating is deliberate. Page 2 of a race list is a place
 * nobody visits, and a round is found by name or by scanning, not by page number.
 */

"use client";

import { useMemo, useState } from "react";
import Link from "next/link";

import type { SessionSummary } from "@/api/client";

/** Enough to fill a screen and show the shape of a season; less is a stub. */
const COLLAPSED = 10;

export function RaceList({ races }: { races: SessionSummary[] }) {
  const [query, setQuery] = useState("");
  const [upsetsOnly, setUpsetsOnly] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const upsetCount = useMemo(
    () => races.filter((r) => r.pace_winner_mismatch).length,
    [races],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return races.filter((race) => {
      if (upsetsOnly && !race.pace_winner_mismatch) return false;
      if (!needle) return true;
      // Round number included so "14" finds round 14 as well as any race named it.
      return (
        race.event_name.toLowerCase().includes(needle) ||
        String(race.round) === needle ||
        (race.headline ?? "").toLowerCase().includes(needle)
      );
    });
  }, [races, query, upsetsOnly]);

  // A filtered list is already short; collapsing it again would hide the results.
  const filtering = query.trim() !== "" || upsetsOnly;
  const visible = expanded || filtering ? filtered : filtered.slice(0, COLLAPSED);
  const hidden = filtered.length - visible.length;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter by race or round"
          aria-label="Filter races"
          className="w-56 rounded border px-2 py-1 text-xs outline-none"
          style={{
            borderColor: "var(--border)",
            background: "var(--surface-1)",
            color: "var(--text-primary)",
          }}
        />

        {upsetCount > 0 && (
          <button
            type="button"
            onClick={() => setUpsetsOnly((on) => !on)}
            aria-pressed={upsetsOnly}
            className="rounded border px-2 py-1 text-xs transition-colors"
            style={{
              borderColor: upsetsOnly ? "var(--series-2)" : "var(--border)",
              background: upsetsOnly ? "var(--surface-2)" : "var(--surface-1)",
              color: upsetsOnly ? "var(--series-2)" : "var(--text-secondary)",
            }}
            title="Races where the quickest car did not win"
          >
            upsets only ({upsetCount})
          </button>
        )}

        {filtering && (
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            {filtered.length} of {races.length}
          </span>
        )}
      </div>

      {filtered.length === 0 ? (
        <p className="px-1 py-3 text-xs" style={{ color: "var(--text-muted)" }}>
          No race matches that filter.
        </p>
      ) : (
        <div
          className="overflow-hidden rounded-lg border"
          style={{ borderColor: "var(--border)" }}
        >
          {visible.map((race, index) => (
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
      )}

      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2 text-xs underline underline-offset-2"
          style={{ color: "var(--text-secondary)" }}
        >
          Show {hidden} more {hidden === 1 ? "round" : "rounds"}
        </button>
      )}
      {expanded && !filtering && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="mt-2 text-xs underline underline-offset-2"
          style={{ color: "var(--text-secondary)" }}
        >
          Show fewer
        </button>
      )}
    </div>
  );
}
