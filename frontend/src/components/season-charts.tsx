/**
 * Season pace curve.
 *
 * One line per driver: gap to the quickest car, round by round. This is the form that
 * shows a season's *shape* — who improved, who fell away, where a car's upgrade landed
 * — none of which a per-race ranking can express.
 *
 * Twenty-two lines on one axis is a thicket, so the chart does three things to stay
 * readable. Only the leading drivers get a colour, and the rest form a single muted
 * band that gives context without pretending each strand is followable. Clicking a
 * driver isolates them against that band. And every line is labelled at its right-hand
 * end, where the eye already is, so reading one costs no trip to a legend.
 */

"use client";

import { useState } from "react";

import type { SeasonPaceResponse } from "@/api/client";

// The validated categorical order. Never cycled: a seventh series would repeat a hue
// and two drivers would share an identity.
const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
] as const;

const NAMED_SERIES = SERIES.length;

export function SeasonPaceChart({ data }: { data: SeasonPaceResponse }) {
  // Which driver is isolated. Click to pin, click again to release — hover alone is
  // no use on a touchscreen and forces a steady hand on a 2px line.
  const [selected, setSelected] = useState<string | null>(null);
  // Smoothing defaults on. Measured across 2023 the typical race-to-race swing is
  // 0.32-0.64 s for most drivers — comparable to their whole mean gap — so circuit
  // character dominates the raw line and the season's shape is invisible under it.
  const [smooth, setSmooth] = useState(true);

  const rounds = data.rounds;
  if (rounds.length < 2 || data.drivers.length === 0) return null;

  const named = data.drivers.slice(0, NAMED_SERIES);
  const rest = data.drivers.slice(NAMED_SERIES);
  const label = (d: (typeof data.drivers)[number]) =>
    d.abbreviation ?? `#${d.driver_number}`;

  const width = 760;
  const height = 320;
  // Right padding leaves room for the end-of-line labels.
  const pad = { top: 14, right: 52, bottom: 40, left: 50 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const allGaps = data.drivers.flatMap((d) =>
    d.gaps.filter((g): g is number => g !== null),
  );
  const maxGap = Math.max(...allGaps, 0.5);

  const x = (index: number) => pad.left + (index / (rounds.length - 1)) * plotW;
  // Zero at the BOTTOM: the quickest car sits on the baseline and falling behind
  // rises away from it. The inverse reads backwards — a line climbing as a driver
  // improves — and the caption on this chart contradicted the drawing for exactly
  // that reason.
  const y = (gap: number) => pad.top + plotH - (gap / maxGap) * plotH;

  /**
   * Centred 3-race rolling mean, skipping absences.
   *
   * Not a model — just enough to let a trend show through circuit-to-circuit noise.
   * A round with no result stays null so the line still breaks there rather than
   * being invented from its neighbours.
   */
  const smoothed = (gaps: (number | null)[]): (number | null)[] =>
    gaps.map((gap, i) => {
      if (gap === null) return null;
      const window = [gaps[i - 1], gaps[i], gaps[i + 1]].filter(
        (g): g is number => g !== null && g !== undefined,
      );
      return window.reduce((a, b) => a + b, 0) / window.length;
    });

  const series = (driver: (typeof data.drivers)[number]) =>
    smooth ? smoothed(driver.gaps) : driver.gaps;

  /** A line, broken where a driver missed a race rather than bridged across it. */
  const path = (gaps: (number | null)[]) => {
    let d = "";
    let pen = false;
    gaps.forEach((gap, i) => {
      if (gap === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(gap).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  /** Where a driver's line ends, for the label anchored there. */
  const lastPoint = (gaps: (number | null)[]) => {
    for (let i = gaps.length - 1; i >= 0; i -= 1) {
      const gap = gaps[i];
      if (gap !== null) return { x: x(i), y: y(gap) };
    }
    return null;
  };

  /**
   * End-of-line label positions, pushed apart where lines finish close together.
   *
   * Six drivers within a second of each other end within a few pixels, and the raw
   * labels stack into an unreadable smudge. Walking them in order and enforcing a
   * minimum spacing keeps every one legible; the leader line still points at the
   * exact value, so a nudged label costs nothing.
   */
  const MIN_LABEL_GAP = 11;
  const labelPositions = (() => {
    const entries = named
      .map((driver, index) => ({ driver, index, point: lastPoint(series(driver)) }))
      .filter((e): e is typeof e & { point: { x: number; y: number } } =>
        e.point !== null,
      )
      .sort((a, b) => a.point.y - b.point.y);

    let previous = -Infinity;
    const placed = new Map<string, number>();
    for (const entry of entries) {
      const y = Math.max(entry.point.y, previous + MIN_LABEL_GAP);
      placed.set(entry.driver.driver_number, y);
      previous = y;
    }
    return placed;
  })();

  // Roughly every fourth round, always including the first and last.
  const tickEvery = Math.max(1, Math.ceil(rounds.length / 6));
  const ticks = rounds
    .map((round, i) => ({ round, i }))
    .filter(({ i }) => i % tickEvery === 0 || i === rounds.length - 1);

  return (
    <div>
      <div className="mb-2 flex justify-end">
        <button
          type="button"
          onClick={() => setSmooth((on) => !on)}
          aria-pressed={smooth}
          className="rounded border px-2 py-0.5 text-xs transition-colors"
          style={{
            borderColor: smooth ? "var(--text-primary)" : "var(--border)",
            background: smooth ? "var(--surface-2)" : "var(--surface-1)",
            color: smooth ? "var(--text-primary)" : "var(--text-secondary)",
          }}
          title="A centred three-race mean, so the season trend shows through circuit-to-circuit variation"
        >
          {smooth ? "smoothed" : "per race"}
        </button>
      </div>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label={`Pace gap to the quickest car by round, ${data.season}`}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={pad.top + plotH - f * plotH}
              y2={pad.top + plotH - f * plotH}
              stroke="var(--border)"
              strokeWidth={1}
              // The zero line is the reference every gap is measured from.
              opacity={f === 0 ? 1 : 0.45}
            />
            <text
              x={pad.left - 8}
              y={pad.top + plotH - f * plotH + 4}
              textAnchor="end"
              fontSize={10}
              fill="var(--text-muted)"
            >
              {f === 0 ? "0" : `+${(f * maxGap).toFixed(1)}s`}
            </text>
          </g>
        ))}

        {ticks.map(({ round, i }) => (
          <text
            key={round}
            x={x(i)}
            y={height - 18}
            textAnchor="middle"
            fontSize={10}
            fill="var(--text-muted)"
          >
            {round}
          </text>
        ))}
        <text
          x={pad.left + plotW / 2}
          y={height - 4}
          textAnchor="middle"
          fontSize={10}
          fill="var(--text-muted)"
        >
          round
        </text>

        {/* The rest of the field first, so it sits behind the named series. */}
        {rest.map((driver) => (
          <path
            key={driver.driver_number}
            d={path(series(driver))}
            fill="none"
            stroke="var(--text-muted)"
            strokeWidth={1}
            opacity={selected ? 0.06 : 0.16}
          />
        ))}

        {named.map((driver, index) => {
          const dimmed = selected !== null && selected !== driver.driver_number;
          const end = lastPoint(series(driver));
          const labelY = labelPositions.get(driver.driver_number);
          return (
            <g key={driver.driver_number}>
              <path
                d={path(series(driver))}
                fill="none"
                stroke={SERIES[index]}
                strokeWidth={selected === driver.driver_number ? 2.5 : 1.75}
                opacity={dimmed ? 0.18 : 1}
              />
              {end && (
                <>
                  {/* A leader from the line's true end to its nudged label, so a
                      displaced label still points at the value it belongs to. */}
                  <line
                    x1={end.x + 2}
                    y1={end.y}
                    x2={end.x + 5}
                    y2={labelY ?? end.y}
                    stroke={SERIES[index]}
                    strokeWidth={1}
                    opacity={dimmed ? 0.25 : 0.6}
                  />
                  <text
                    x={end.x + 7}
                    y={(labelY ?? end.y) + 3}
                    fontSize={10}
                    fontWeight={selected === driver.driver_number ? 700 : 500}
                    fill={SERIES[index]}
                    opacity={dimmed ? 0.25 : 1}
                  >
                    {label(driver)}
                  </text>
                </>
              )}
            </g>
          );
        })}
      </svg>

      {/* The table doubles as the legend and the selector: a line's exact value is
          hard to read off a chart, and the numbers are half the point. */}
      <div className="mt-4 space-y-1">
        <div
          className="flex items-center gap-3 border-b pb-1.5 text-xs"
          style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
        >
          <span className="w-3" />
          <span className="w-12">driver</span>
          <span className="w-20 text-right">mean gap</span>
          <span className="w-16 text-right">best</span>
          <span className="flex-1 pl-3">races quickest</span>
        </div>

        {named.map((driver, index) => {
          const isSelected = selected === driver.driver_number;
          return (
            <button
              key={driver.driver_number}
              type="button"
              onClick={() =>
                setSelected(isSelected ? null : driver.driver_number)
              }
              aria-pressed={isSelected}
              className="flex w-full items-center gap-3 rounded px-0.5 py-0.5 text-left text-xs transition-colors"
              style={{
                background: isSelected ? "var(--surface-2)" : "transparent",
                opacity: selected && !isSelected ? 0.45 : 1,
              }}
            >
              <span
                aria-hidden
                className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
                style={{ background: SERIES[index] }}
              />
              <span className="w-12 font-medium">{label(driver)}</span>
              <span className="tnum w-20 text-right">
                +{driver.mean_gap_s.toFixed(3)}s
              </span>
              <span
                className="tnum w-16 text-right"
                style={{ color: "var(--text-muted)" }}
              >
                +{driver.best_gap_s.toFixed(3)}
              </span>
              <span className="flex-1 pl-3" style={{ color: "var(--text-muted)" }}>
                <span className="tnum font-medium" style={{ color: "var(--text-primary)" }}>
                  {driver.wins_on_pace}
                </span>{" "}
                of {driver.n_races} run
              </span>
            </button>
          );
        })}

        {rest.length > 0 && (
          <p className="pt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
            {rest.length} further drivers drawn unlabelled — the palette carries six
            distinguishable hues, and a seventh would repeat one.
            {selected && " Click the highlighted driver again to show all."}
          </p>
        )}
      </div>
    </div>
  );
}
