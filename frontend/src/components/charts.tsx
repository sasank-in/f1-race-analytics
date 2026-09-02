/**
 * Charts, built as plain SVG.
 *
 * No charting library: these are simple forms, and hand-drawn SVG keeps the marks
 * exactly to spec — 2px lines, 4px rounded data ends, a 2px surface gap between
 * adjacent fills — without fighting a library's defaults.
 *
 * Every chart here direct-labels its values. The light palette raises a contrast
 * warning on two slots, and the obligation that creates is visible labels, so the
 * numbers are on the marks rather than only in a tooltip.
 */

"use client";

import { useState } from "react";

import { formatGap, formatLapTime } from "@/api/client";
import type { DegradationResponse, PaceResponse } from "@/api/client";
import { compoundColor } from "./ui";

/**
 * Pace gaps as a horizontal bar chart.
 *
 * Horizontal because driver labels are text and read far better along the y-axis
 * than rotated under a vertical one. Bars start at zero: the leader has no bar,
 * which is correct — they are the reference, not a zero-length measurement.
 */
export function PaceChart({ data }: { data: PaceResponse }) {
  const [hovered, setHovered] = useState<string | null>(null);
  const drivers = data.drivers;
  if (drivers.length === 0) return null;

  const maxGap = Math.max(...drivers.map((d) => d.gap_to_best_s), 0.001);
  const rowHeight = 26;

  return (
    <div className="space-y-1">
      {/* Column headers. These label what follows, so they belong above the rows —
          the same text underneath reads as a footnote and is found only after the
          reader has already guessed what each column meant. */}
      <div
        className="flex gap-3 border-b pb-1.5 text-xs"
        style={{ color: "var(--text-muted)", borderColor: "var(--border)" }}
      >
        <span className="w-5" />
        <span className="w-8">car</span>
        <span className="flex-1">gap to fastest</span>
        <span className="w-16 text-right">gap</span>
        <span className="w-20 text-right">pace</span>
        <span className="w-10 text-right">laps</span>
        <span className="w-14 text-right">result</span>
      </div>
      {drivers.map((driver) => {
        const width = (driver.gap_to_best_s / maxGap) * 100;
        const isHovered = hovered === driver.driver_number;
        return (
          <div
            key={driver.driver_number}
            className="flex items-center gap-3 text-xs"
            style={{ height: rowHeight }}
            onMouseEnter={() => setHovered(driver.driver_number)}
            onMouseLeave={() => setHovered(null)}
          >
            <span
              className="tnum w-5 text-right"
              style={{ color: "var(--text-muted)" }}
            >
              {driver.rank}
            </span>
            <span className="tnum w-8 font-medium">#{driver.driver_number}</span>

            <div className="relative flex-1">
              {driver.rank === 1 ? (
                // The leader is the reference, not a zero-length measurement. A tick
                // plus a label says that; a 1px bar reads as a rendering fault.
                <div className="flex h-4 items-center gap-2">
                  <span
                    aria-hidden
                    className="inline-block h-4 w-1 rounded-sm"
                    style={{ background: "var(--series-1)" }}
                  />
                  <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                    reference
                  </span>
                </div>
              ) : (
                <div
                  className="h-4 rounded-r"
                  style={{
                    width: `${Math.max(width, 0.8)}%`,
                    background: "var(--series-2)",
                    opacity: isHovered ? 1 : 0.85,
                    transition: "opacity 120ms",
                  }}
                />
              )}
            </div>

            <span className="tnum w-16 text-right" style={{ color: "var(--text-secondary)" }}>
              {formatGap(driver.gap_to_best_s)}
            </span>
            <span className="tnum w-20 text-right font-medium">
              {formatLapTime(driver.pace_s)}
            </span>
            <span
              className="tnum w-10 text-right"
              style={{ color: "var(--text-muted)" }}
              title={`${driver.n_laps} clean laps`}
            >
              {driver.n_laps}
            </span>

            {/* Outcome beside pace. A car can be third-quickest and finish nowhere;
                without this the ranking reads as a finishing order. */}
            <span
              className="tnum w-14 text-right"
              style={{
                color: driver.did_not_finish
                  ? "var(--critical)"
                  : "var(--text-secondary)",
              }}
              title={driver.status ?? undefined}
            >
              {driver.did_not_finish
                ? "DNF"
                : driver.finish_position
                  ? `P${driver.finish_position.toFixed(0)}`
                  : "—"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Degradation by compound.
 *
 * The bar is the median slope; the whisker is the interquartile spread across
 * stints. Showing the spread is the point — a strategy model handed only a centre
 * presents a pit window as a single lap.
 */
export function DegradationChart({ data }: { data: DegradationResponse }) {
  const compounds = data.compounds;
  if (compounds.length === 0) return null;

  const maxValue = Math.max(
    ...compounds.map((c) => c.degradation_s_per_lap + c.degradation_iqr_s / 2),
    0.05,
  );

  return (
    <div className="space-y-3">
      {compounds.map((compound) => {
        const centre = (compound.degradation_s_per_lap / maxValue) * 100;
        const halfSpread = (compound.degradation_iqr_s / 2 / maxValue) * 100;
        return (
          <div key={compound.compound} className="text-xs">
            <div className="mb-1 flex items-baseline justify-between">
              <span className="font-medium">{compound.compound}</span>
              <span className="tnum" style={{ color: "var(--text-secondary)" }}>
                {compound.degradation_s_per_lap >= 0 ? "+" : ""}
                {compound.degradation_s_per_lap.toFixed(3)} s/lap
              </span>
            </div>
            <div
              className="relative h-4 rounded-sm"
              style={{ background: "var(--surface-2)" }}
            >
              <div
                className="absolute h-4 rounded-r"
                style={{
                  width: `${Math.max(centre, 0.8)}%`,
                  background: compoundColor(compound.compound),
                  boxShadow: "0 0 0 1px var(--border-strong)",
                }}
              />
              {/* Interquartile range, centred on the median and drawn as a bracket
                  so it reads as the width of the estimate rather than a second bar. */}
              {compound.degradation_iqr_s > 0 && (
                <div
                  className="absolute top-0 h-4 border-x-2"
                  style={{
                    left: `${Math.max(centre - halfSpread, 0)}%`,
                    width: `${Math.min(halfSpread * 2, 100)}%`,
                    borderColor: "var(--text-primary)",
                    opacity: 0.5,
                  }}
                />
              )}
            </div>
            <div className="mt-1" style={{ color: "var(--text-muted)" }}>
              {compound.n_stints} stints · longest {compound.max_stint_laps} laps ·
              spread {compound.degradation_iqr_s.toFixed(3)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Cumulative delta time between two laps, plotted against distance.
 *
 * The zero line is the reference lap. Below it the comparison lap is ahead; above
 * it behind. Read as a shape this diagnoses the lap — a step down under braking is
 * a later brake point, a rise after a corner means the time went in the corner.
 */
export function DeltaTrace({
  distance,
  delta,
  referenceDriver,
  comparisonDriver,
}: {
  distance: number[];
  delta: number[];
  referenceDriver: string;
  comparisonDriver: string;
}) {
  const [cursor, setCursor] = useState<number | null>(null);
  if (distance.length < 2) return null;

  const width = 720;
  const height = 200;
  const pad = { top: 12, right: 12, bottom: 28, left: 44 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const maxDistance = distance[distance.length - 1];
  const bound = Math.max(...delta.map(Math.abs), 0.05);

  const x = (d: number) => pad.left + (d / maxDistance) * plotW;
  const y = (v: number) => pad.top + plotH / 2 - (v / bound) * (plotH / 2);

  const path = distance
    .map((d, i) => `${i === 0 ? "M" : "L"}${x(d).toFixed(1)},${y(delta[i]).toFixed(1)}`)
    .join("");

  const cursorIndex =
    cursor === null
      ? null
      : Math.min(
          distance.length - 1,
          Math.max(0, Math.round(((cursor - pad.left) / plotW) * (distance.length - 1))),
        );

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label={`Cumulative time delta between car ${comparisonDriver} and car ${referenceDriver} around the lap`}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          setCursor(((e.clientX - rect.left) / rect.width) * width);
        }}
        onMouseLeave={() => setCursor(null)}
      >
        {/* Zero line: the reference lap. */}
        <line
          x1={pad.left}
          x2={width - pad.right}
          y1={y(0)}
          y2={y(0)}
          stroke="var(--border-strong)"
          strokeWidth={1}
        />
        <path d={path} fill="none" stroke="var(--series-1)" strokeWidth={2} />

        {cursorIndex !== null && (
          <>
            <line
              x1={x(distance[cursorIndex])}
              x2={x(distance[cursorIndex])}
              y1={pad.top}
              y2={pad.top + plotH}
              stroke="var(--text-muted)"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <circle
              cx={x(distance[cursorIndex])}
              cy={y(delta[cursorIndex])}
              r={4}
              fill="var(--series-1)"
              stroke="var(--surface-1)"
              strokeWidth={2}
            />
          </>
        )}

        <text x={pad.left - 6} y={y(bound) + 4} textAnchor="end" fontSize={10} fill="var(--text-muted)">
          +{bound.toFixed(2)}
        </text>
        <text x={pad.left - 6} y={y(0) + 4} textAnchor="end" fontSize={10} fill="var(--text-muted)">
          0
        </text>
        <text x={pad.left - 6} y={y(-bound) + 4} textAnchor="end" fontSize={10} fill="var(--text-muted)">
          −{bound.toFixed(2)}
        </text>
        <text x={pad.left} y={height - 8} fontSize={10} fill="var(--text-muted)">
          0 m
        </text>
        <text x={width - pad.right} y={height - 8} textAnchor="end" fontSize={10} fill="var(--text-muted)">
          {Math.round(maxDistance)} m
        </text>
      </svg>

      <p className="mt-1 text-xs tnum" style={{ color: "var(--text-secondary)" }}>
        {cursorIndex !== null ? (
          <>
            {Math.round(distance[cursorIndex])} m — car {comparisonDriver} is{" "}
            <strong>{formatGap(delta[cursorIndex])} s</strong>{" "}
            {delta[cursorIndex] < 0 ? "ahead" : "behind"}
          </>
        ) : (
          <>Below the line, car {comparisonDriver} is ahead of car {referenceDriver}.</>
        )}
      </p>
    </div>
  );
}
