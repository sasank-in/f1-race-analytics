/**
 * Stint fits — the audit trail behind every degradation number.
 *
 * This is the table that lets someone check the model rather than trust it: every
 * fitted stint, including the ones that failed, with the r² that says how well the
 * line actually described the run.
 *
 * Sixty-five undifferentiated rows bury the dozen that matter, though. The fits worth
 * looking at are the ones that failed or barely held, so those lead and the rest are
 * one click away. An earlier version silently cut the list at 40 of 65, which is worse
 * than either: it hid a third of the evidence while claiming to show "every" stint.
 */

"use client";

import { useState } from "react";

import { formatLapTime, type StintFit } from "@/api/client";
import { CompoundTag } from "./ui";

/** Below this the line explained less than half the stint's variance. */
const WEAK_FIT = 0.5;

/**
 * `is_reliable` is deliberately NOT used to flag a row here.
 *
 * It fails a stint whose slope exceeds MAX_PLAUSIBLE_DEG_S_PER_LAP (0.22 s/lap) — a
 * bound taken from published *circuit medians*. A single stint at a harsh circuit can
 * legitimately exceed it, and 53 fits in this dataset sit between 0.22 and 0.35 with
 * r-squared above 0.8: well-described runs, not failures. Flagging them as needing a
 * caveat alongside genuinely broken fits would tell the reader the wrong thing.
 *
 * What does earn a caveat: a negative slope (the fit failed outright) and a weak
 * r-squared (the line did not describe the run).
 */

export function StintFits({ stints }: { stints: StintFit[] }) {
  const [showAll, setShowAll] = useState(false);

  if (stints.length === 0) return null;

  const suspect = (s: StintFit) =>
    s.is_physical === false || (s.r_squared ?? 1) < WEAK_FIT;

  const flagged = stints.filter(suspect);
  const clean = stints.filter((s) => !suspect(s));
  const shown = showAll ? [...flagged, ...clean] : flagged;

  return (
    <div>
      <p className="mb-3 text-xs" style={{ color: "var(--text-secondary)" }}>
        {flagged.length === 0 ? (
          <>All {stints.length} stint fits are physical and explain their run.</>
        ) : (
          <>
            <span style={{ color: "var(--text-primary)" }}>
              {flagged.length} of {stints.length}
            </span>{" "}
            fits need a caveat — a negative slope, or a line explaining less than half
            the stint. They are listed first; the rest held.
          </>
        )}
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr style={{ color: "var(--text-muted)" }}>
              <th className="pb-2 text-left font-normal">driver</th>
              <th className="pb-2 text-left font-normal">stint</th>
              <th className="pb-2 text-left font-normal">compound</th>
              <th className="pb-2 text-right font-normal">laps</th>
              <th className="pb-2 text-right font-normal">pace</th>
              <th className="pb-2 text-right font-normal">deg s/lap</th>
              <th className="pb-2 text-right font-normal">r²</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((stint) => {
              const unphysical = stint.is_physical === false;
              const weak = (stint.r_squared ?? 1) < WEAK_FIT;
              return (
                <tr
                  key={`${stint.driver_number}-${stint.stint}`}
                  style={{
                    color: unphysical
                      ? "var(--text-muted)"
                      : "var(--text-secondary)",
                  }}
                >
                  <td className="py-1 font-medium">
                    {stint.abbreviation ?? `#${stint.driver_number}`}
                  </td>
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
                      color: unphysical ? "var(--warning)" : "var(--text-primary)",
                    }}
                    title={
                      unphysical
                        ? "Negative slope: the stint was too short to support an estimate"
                        : undefined
                    }
                  >
                    {stint.degradation_s_per_lap >= 0 ? "+" : ""}
                    {stint.degradation_s_per_lap.toFixed(3)}
                  </td>
                  <td
                    className="tnum py-1 text-right"
                    style={{ color: weak ? "var(--warning)" : undefined }}
                    title={
                      weak
                        ? "The line explained less than half the stint's variance — traffic, a mistake, or changing conditions"
                        : undefined
                    }
                  >
                    {stint.r_squared?.toFixed(2) ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {clean.length > 0 && (
        <button
          type="button"
          onClick={() => setShowAll((on) => !on)}
          className="mt-3 text-xs underline underline-offset-2"
          style={{ color: "var(--text-secondary)" }}
        >
          {showAll
            ? "Show only the fits needing a caveat"
            : `Show the ${clean.length} fits that held`}
        </button>
      )}
    </div>
  );
}
