/**
 * Race position by lap.
 *
 * The one chart every F1 broadcast shows and this application did not: who was where,
 * lap by lap. It answers a different question from the pace ranking — pace says which
 * car was quickest, this says what actually happened to the order, and the gap between
 * the two is where overtakes, stops and safety cars live.
 *
 * P1 sits at the top, because that is how a running order is read everywhere else.
 * Inverting it to satisfy a mathematical axis would be technically consistent and
 * wrong for the reader.
 */

"use client";

import { useMemo, useState } from "react";

import type { Lap } from "@/api/client";

const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
] as const;

/** One driver's run through the race. */
type Track = {
  driver: string;
  label: string;
  points: { lap: number; position: number }[];
  start: number;
  finish: number;
  /** Last lap with a position. Short of the race distance means they retired. */
  lastLap: number;
};

export function PositionChart({
  laps,
  codes,
}: {
  laps: Lap[];
  /** Driver number to three-letter code, from the pace ranking. */
  codes: Map<string, string>;
}) {
  const [selected, setSelected] = useState<string | null>(null);

  const { tracks, maxLap, maxPosition } = useMemo(() => {
    const byDriver = new Map<string, { lap: number; position: number }[]>();
    let lastLap = 0;
    let lowest = 0;

    for (const lap of laps) {
      if (lap.position == null) continue;
      lastLap = Math.max(lastLap, lap.lap_number);
      lowest = Math.max(lowest, lap.position);
      const points = byDriver.get(lap.driver_number) ?? [];
      points.push({ lap: lap.lap_number, position: lap.position });
      byDriver.set(lap.driver_number, points);
    }

    const built: Track[] = [];
    for (const [driver, points] of byDriver) {
      points.sort((a, b) => a.lap - b.lap);
      if (points.length < 2) continue;
      built.push({
        driver,
        label: codes.get(driver) ?? `#${driver}`,
        points,
        start: points[0].position,
        finish: points[points.length - 1].position,
        lastLap: points[points.length - 1].lap,
      });
    }

    // Ordered by where they ended up, so the legend reads like a result.
    // Classified finishers first, then retirements: a car that stopped on lap 32 was
    // not "P3", it was running third when it stopped.
    built.sort((a, b) => {
      const aOut = a.lastLap < lastLap - 1;
      const bOut = b.lastLap < lastLap - 1;
      if (aOut !== bOut) return aOut ? 1 : -1;
      return a.finish - b.finish;
    });
    return { tracks: built, maxLap: lastLap, maxPosition: lowest };
  }, [laps, codes]);

  if (tracks.length === 0 || maxLap < 2) return null;

  const width = 760;
  const height = 340;
  const pad = { top: 14, right: 46, bottom: 34, left: 34 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const x = (lap: number) => pad.left + ((lap - 1) / (maxLap - 1)) * plotW;
  // P1 at the top: a running order counts downward, and every other place this is
  // shown does the same.
  const y = (position: number) =>
    pad.top + ((position - 1) / Math.max(maxPosition - 1, 1)) * plotH;

  const path = (points: { lap: number; position: number }[]) =>
    points
      .map((p, i) => `${i === 0 ? "M" : "L"}${x(p.lap).toFixed(1)},${y(p.position).toFixed(1)}`)
      .join("");

  // Only the front runners get a colour; the rest are context.
  const named = tracks.slice(0, SERIES.length);
  const rest = tracks.slice(SERIES.length);
  const colourOf = (driver: string) => {
    const index = named.findIndex((t) => t.driver === driver);
    return index >= 0 ? SERIES[index] : "var(--text-muted)";
  };

  const lapTicks = Array.from(
    new Set([1, ...[0.25, 0.5, 0.75].map((f) => Math.round(f * maxLap)), maxLap]),
  ).filter((lap) => lap >= 1 && lap <= maxLap);

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label="Race position by lap"
      >
        {[1, 5, 10, 15, 20].
          filter((p) => p <= maxPosition).
          map((position) => (
            <g key={position}>
              <line
                x1={pad.left}
                x2={width - pad.right}
                y1={y(position)}
                y2={y(position)}
                stroke="var(--border)"
                strokeWidth={1}
                opacity={position === 1 ? 1 : 0.45}
              />
              <text
                x={pad.left - 6}
                y={y(position) + 3}
                textAnchor="end"
                fontSize={10}
                fill="var(--text-muted)"
              >
                P{position}
              </text>
            </g>
          ))}

        {rest.map((track) => (
          <path
            key={track.driver}
            d={path(track.points)}
            fill="none"
            stroke="var(--text-muted)"
            strokeWidth={1}
            opacity={selected ? 0.05 : 0.16}
          />
        ))}

        {named.map((track) => {
          const dimmed = selected !== null && selected !== track.driver;
          const end = track.points[track.points.length - 1];
          return (
            <g key={track.driver}>
              <path
                d={path(track.points)}
                fill="none"
                stroke={colourOf(track.driver)}
                strokeWidth={selected === track.driver ? 2.5 : 1.75}
                opacity={dimmed ? 0.18 : 1}
              />
              {/* A retired car's line stops mid-chart, so its label would sit on top
                  of whoever is still running there. Halo it and mark the retirement. */}
              <text
                x={x(end.lap) + 5}
                y={y(end.position) + 3}
                fontSize={10}
                fontWeight={selected === track.driver ? 700 : 500}
                fill={colourOf(track.driver)}
                opacity={dimmed ? 0.25 : 1}
                stroke="var(--surface-1)"
                strokeWidth={3}
                paintOrder="stroke"
              >
                {track.label}
                {track.lastLap < maxLap - 1 ? " ✕" : ""}
              </text>
            </g>
          );
        })}

        {lapTicks.map((lap) => (
          <text
            key={lap}
            x={x(lap)}
            y={height - 16}
            textAnchor="middle"
            fontSize={10}
            fill="var(--text-muted)"
          >
            {lap}
          </text>
        ))}
        <text
          x={pad.left + plotW / 2}
          y={height - 3}
          textAnchor="middle"
          fontSize={10}
          fill="var(--text-muted)"
        >
          lap
        </text>
      </svg>

      <div className="mt-3 space-y-1">
        <div
          className="flex items-center gap-3 border-b pb-1.5 text-xs"
          style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
        >
          <span className="w-3" />
          <span className="w-12">driver</span>
          <span className="w-14 text-right">started</span>
          <span className="w-14 text-right">finished</span>
          <span className="flex-1 pl-3">net</span>
        </div>

        {named.map((track) => {
          const retired = track.lastLap < maxLap - 1;
          const gained = track.start - track.finish;
          const isSelected = selected === track.driver;
          return (
            <button
              key={track.driver}
              type="button"
              onClick={() => setSelected(isSelected ? null : track.driver)}
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
                style={{ background: colourOf(track.driver) }}
              />
              <span className="w-12 font-medium">{track.label}</span>
              <span className="tnum w-14 text-right" style={{ color: "var(--text-muted)" }}>
                P{track.start}
              </span>
              <span
                className="tnum w-14 text-right font-medium"
                style={retired ? { color: "var(--critical)" } : undefined}
                title={retired ? `Retired on lap ${track.lastLap}` : undefined}
              >
                {retired ? "DNF" : `P${track.finish}`}
              </span>
              <span
                className="tnum flex-1 pl-3"
                style={{
                  color: retired
                    ? "var(--text-muted)"
                    : gained > 0
                      ? "var(--good)"
                      : gained < 0
                        ? "var(--critical)"
                        : "var(--text-muted)",
                }}
              >
                {retired
                  ? `retired lap ${track.lastLap}`
                  : gained > 0
                    ? `+${gained}`
                    : gained < 0
                      ? `${gained}`
                      : "—"}
              </span>
            </button>
          );
        })}
      </div>

      <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
        Position on the road at the end of each lap, so a car in the pits shows as
        dropping and recovering rather than as losing places on track.
      </p>
    </div>
  );
}
