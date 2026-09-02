/**
 * Season pace curve.
 *
 * A multi-series line: one per driver, gap to the quickest car against round number.
 * This is the form that shows a season's *shape* — who improved, who fell away, where
 * a car's upgrade actually landed — none of which a per-race ranking can express.
 *
 * The field is twenty-plus drivers and the categorical palette carries eight slots,
 * assigned in fixed order and never cycled. So only the leading drivers are drawn as
 * identified series; the rest are a single muted band giving context without pretending
 * each has an identity the eye could track.
 */

"use client";

import { useState } from "react";

import type { SeasonPaceResponse } from "@/api/client";
import { Legend } from "./ui";

// The validated categorical order. Never cycled: a ninth series would repeat a hue
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
  const [hovered, setHovered] = useState<string | null>(null);
  const rounds = data.rounds;
  if (rounds.length < 2 || data.drivers.length === 0) return null;

  const named = data.drivers.slice(0, NAMED_SERIES);
  const rest = data.drivers.slice(NAMED_SERIES);

  const width = 760;
  const height = 300;
  const pad = { top: 16, right: 16, bottom: 34, left: 46 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const allGaps = data.drivers.flatMap((d) =>
    d.gaps.filter((g): g is number => g !== null),
  );
  const maxGap = Math.max(...allGaps, 0.5);

  const x = (index: number) => pad.left + (index / (rounds.length - 1)) * plotW;
  const y = (gap: number) => pad.top + (gap / maxGap) * plotH;

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

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label={`Pace gap to the quickest car by round, ${data.season}`}
      >
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={pad.top + f * plotH}
              y2={pad.top + f * plotH}
              stroke="var(--border)"
              strokeWidth={1}
            />
            <text
              x={pad.left - 6}
              y={pad.top + f * plotH + 4}
              textAnchor="end"
              fontSize={10}
              fill="var(--text-muted)"
            >
              +{(f * maxGap).toFixed(1)}s
            </text>
          </g>
        ))}

        {/* The rest of the field first, so it sits behind the named series. */}
        {rest.map((driver) => (
          <path
            key={driver.driver_number}
            d={path(driver.gaps)}
            fill="none"
            stroke="var(--text-muted)"
            strokeWidth={1}
            opacity={hovered ? 0.08 : 0.18}
          />
        ))}

        {named.map((driver, index) => (
          <path
            key={driver.driver_number}
            d={path(driver.gaps)}
            fill="none"
            stroke={SERIES[index]}
            strokeWidth={hovered === driver.driver_number ? 3 : 2}
            opacity={hovered && hovered !== driver.driver_number ? 0.25 : 1}
            onMouseEnter={() => setHovered(driver.driver_number)}
            onMouseLeave={() => setHovered(null)}
            style={{ cursor: "pointer" }}
          />
        ))}

        <text x={pad.left} y={height - 10} fontSize={10} fill="var(--text-muted)">
          round {rounds[0]}
        </text>
        <text
          x={width - pad.right}
          y={height - 10}
          textAnchor="end"
          fontSize={10}
          fill="var(--text-muted)"
        >
          round {rounds[rounds.length - 1]}
        </text>
      </svg>

      <div className="mt-3">
        <Legend
          items={named.map((driver, index) => ({
            label: `#${driver.driver_number}`,
            color: SERIES[index],
          }))}
        />
      </div>

      {/* Direct figures for the named series, since a line's exact value is hard to
          read off a chart and the numbers are the point. */}
      <div className="mt-4 space-y-1">
        {named.map((driver, index) => (
          <div
            key={driver.driver_number}
            className="flex items-center gap-3 text-xs"
            onMouseEnter={() => setHovered(driver.driver_number)}
            onMouseLeave={() => setHovered(null)}
          >
            <span
              aria-hidden
              className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ background: SERIES[index] }}
            />
            <span className="tnum w-9 font-medium">#{driver.driver_number}</span>
            <span className="tnum w-20 text-right" style={{ color: "var(--text-secondary)" }}>
              +{driver.mean_gap_s.toFixed(3)}s
            </span>
            <span className="w-24 text-xs" style={{ color: "var(--text-muted)" }}>
              mean gap
            </span>
            <span className="tnum w-8 text-right font-medium">
              {driver.wins_on_pace}
            </span>
            <span className="flex-1 text-xs" style={{ color: "var(--text-muted)" }}>
              races quickest of {driver.n_races} run
            </span>
          </div>
        ))}
        {rest.length > 0 && (
          <p className="pt-1 text-xs" style={{ color: "var(--text-muted)" }}>
            {rest.length} further drivers shown unlabelled — the palette carries eight
            distinguishable hues, and a ninth would repeat one.
          </p>
        )}
      </div>
    </div>
  );
}
