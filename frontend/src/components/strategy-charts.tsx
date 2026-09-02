/**
 * Strategy visualisations.
 *
 * Two forms that a bar chart cannot do: a timeline, where the x-axis is race distance
 * and each driver is a row, and a matrix, where two categorical axes meet.
 */

"use client";

import { useState } from "react";

import type { Stint, UndercutWindow } from "@/api/client";
import { compoundColor } from "./ui";

/**
 * Stint timeline — the shape of the race in one view.
 *
 * Each row is a driver, each block a stint, coloured by compound and labelled with
 * its length. Read down a column and you see who pitted together; read across a row
 * and you see one driver's whole strategy. A table of the same numbers cannot show
 * either pattern.
 */
export function StintTimeline({
  stints,
  totalLaps,
}: {
  stints: Stint[];
  totalLaps: number | null | undefined;
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  if (stints.length === 0 || !totalLaps) return null;

  const byDriver = new Map<string, Stint[]>();
  for (const stint of stints) {
    const list = byDriver.get(stint.driver_number) ?? [];
    list.push(stint);
    byDriver.set(stint.driver_number, list);
  }

  // Order by who finished their first stint latest — a proxy for running order that
  // groups similar strategies together rather than sorting by car number.
  const drivers = [...byDriver.entries()].sort(
    (a, b) => (b[1][0]?.end_lap ?? 0) - (a[1][0]?.end_lap ?? 0),
  );

  return (
    <div>
      <div className="space-y-1">
        {drivers.map(([driver, driverStints]) => (
          <div
            key={driver}
            className="flex items-center gap-2"
            onMouseEnter={() => setHovered(driver)}
            onMouseLeave={() => setHovered(null)}
          >
            <span className="tnum w-8 shrink-0 text-xs font-medium">#{driver}</span>
            <div className="relative flex h-5 min-w-0 flex-1 gap-[2px] overflow-hidden">
              {driverStints.map((stint) => {
                const width = (stint.n_laps / totalLaps) * 100;
                return (
                  <div
                    key={stint.stint}
                    className="flex shrink items-center justify-center overflow-hidden rounded-sm text-[10px]"
                    style={{
                      // flex-basis rather than width, so the 2px gaps come out of the
                      // blocks instead of pushing the row past its container.
                      flex: `0 1 ${width}%`,
                      background: compoundColor(stint.compound),
                      opacity: hovered && hovered !== driver ? 0.4 : 1,
                      transition: "opacity 120ms",
                      // Dark text on the light compounds, light on the dark ones.
                      color:
                        stint.compound === "HARD" || stint.compound === "MEDIUM"
                          ? "#0b0b0b"
                          : "#ffffff",
                    }}
                    title={`#${driver} stint ${stint.stint}: laps ${stint.start_lap}–${stint.end_lap} on ${stint.compound ?? "unknown"}${
                      stint.tyre_age_start
                        ? `, started at ${stint.tyre_age_start} laps of age`
                        : ""
                    }`}
                  >
                    {/* Only label a block wide enough to hold the number. */}
                    {width > 7 ? stint.n_laps : ""}
                  </div>
                );
              })}
              {(() => {
                // A retirement leaves the row short. Say so, rather than leaving a
                // gap that reads as a missing block.
                const covered = driverStints.reduce((n, s) => n + s.n_laps, 0);
                const remaining = totalLaps - covered;
                return remaining > 1 ? (
                  <div
                    className="flex items-center rounded-sm border border-dashed pl-1.5 text-[10px]"
                    style={{
                      flex: `0 1 ${(remaining / totalLaps) * 100}%`,
                      borderColor: "var(--border-strong)",
                      color: "var(--text-muted)",
                    }}
                    title={`Classified after ${covered} laps`}
                  >
                    {remaining / totalLaps > 0.12 ? "did not finish" : ""}
                  </div>
                ) : null;
              })()}
            </div>
          </div>
        ))}
      </div>

      <div
        className="mt-3 flex items-center justify-between text-xs"
        style={{ color: "var(--text-muted)" }}
      >
        <span>lap 1</span>
        <span>stint length in laps</span>
        <span>lap {totalLaps}</span>
      </div>
    </div>
  );
}

const VERDICT_STYLE: Record<string, { color: string; label: string }> = {
  undercut: { color: "var(--series-3)", label: "would work" },
  marginal: { color: "var(--series-4)", label: "marginal" },
  hold: { color: "var(--text-muted)", label: "would fail" },
};

/**
 * Undercut opportunities over the race.
 *
 * Every lap where a driver sat within striking distance of the car ahead, scored by
 * whether pitting would have gained the position. Plotted against lap number so the
 * windows — the phases where undercuts were live — are visible as clusters.
 */
export function UndercutChart({
  windows,
  totalLaps,
}: {
  windows: UndercutWindow[];
  totalLaps: number | null | undefined;
}) {
  const [selected, setSelected] = useState<UndercutWindow | null>(null);
  if (windows.length === 0 || !totalLaps) return null;

  const width = 720;
  const height = 220;
  const pad = { top: 12, right: 16, bottom: 32, left: 44 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const maxGap = Math.max(...windows.map((w) => w.gap_s), 1);

  const x = (lap: number) => pad.left + (lap / totalLaps) * plotW;
  const y = (gap: number) => pad.top + (gap / maxGap) * plotH;

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label="Undercut opportunities by lap and gap to the car ahead"
      >
        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={pad.top + fraction * plotH}
              y2={pad.top + fraction * plotH}
              stroke="var(--border)"
              strokeWidth={1}
            />
            <text
              x={pad.left - 6}
              y={pad.top + fraction * plotH + 4}
              textAnchor="end"
              fontSize={10}
              fill="var(--text-muted)"
            >
              {(fraction * maxGap).toFixed(1)}s
            </text>
          </g>
        ))}

        {windows.map((window, index) => (
          <circle
            key={`${window.lap_number}-${window.attacker}-${index}`}
            cx={x(window.lap_number)}
            cy={y(window.gap_s)}
            r={4}
            fill={VERDICT_STYLE[window.verdict]?.color ?? "var(--text-muted)"}
            // A surface ring keeps overlapping points separable.
            stroke="var(--surface-1)"
            strokeWidth={1.5}
            opacity={0.8}
            style={{ cursor: "pointer" }}
            onMouseEnter={() => setSelected(window)}
          />
        ))}

        <text x={pad.left} y={height - 10} fontSize={10} fill="var(--text-muted)">
          lap 1
        </text>
        <text
          x={width - pad.right}
          y={height - 10}
          textAnchor="end"
          fontSize={10}
          fill="var(--text-muted)"
        >
          lap {totalLaps}
        </text>
        <text
          x={width / 2}
          y={height - 10}
          textAnchor="middle"
          fontSize={10}
          fill="var(--text-muted)"
        >
          gap to the car ahead
        </text>
      </svg>

      <div className="mt-2 flex flex-wrap items-center gap-4 text-xs">
        {Object.entries(VERDICT_STYLE).map(([verdict, style]) => (
          <span key={verdict} className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ background: style.color }}
            />
            <span style={{ color: "var(--text-secondary)" }}>{style.label}</span>
          </span>
        ))}
        <span className="tnum ml-auto" style={{ color: "var(--text-muted)" }}>
          {selected
            ? `lap ${selected.lap_number}: #${selected.attacker} chasing #${selected.defender}, gap ${selected.gap_s.toFixed(2)}s, margin ${selected.margin_s >= 0 ? "+" : ""}${selected.margin_s.toFixed(2)}s`
            : `${windows.length} opportunities — hover a point`}
        </span>
      </div>
    </div>
  );
}
