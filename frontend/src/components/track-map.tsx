/**
 * Track map.
 *
 * The circuit drawn from where the car actually went, coloured by speed. Nothing
 * about the layout is stored — the shape comes from the positional trace — so any
 * circuit with data renders without a map to maintain.
 *
 * Speed is a *magnitude*, so it gets a sequential ramp: one hue, light to dark. The
 * categorical palette would be wrong here — it encodes identity, and there is only
 * one thing being shown.
 */

"use client";

import { useState } from "react";

import type { TrackMapResponse } from "@/api/client";

/**
 * Sequential speed ramp, blue.
 *
 * Light where the car is slow, dark where it is fast, so the eye reads the straights
 * as the heavy parts of the line. Lightness moves monotonically across the ramp,
 * which is what makes a sequential scale readable at all.
 */
const SPEED_RAMP = [
  "#dbeafe",
  "#bfdbfe",
  "#93c5fd",
  "#60a5fa",
  "#3b82f6",
  "#2563eb",
  "#1d4ed8",
  "#1e3a8a",
] as const;

function speedColor(speed: number, min: number, max: number): string {
  if (max <= min) return SPEED_RAMP[SPEED_RAMP.length - 1];
  const fraction = (speed - min) / (max - min);
  const index = Math.min(
    SPEED_RAMP.length - 1,
    Math.max(0, Math.floor(fraction * SPEED_RAMP.length)),
  );
  return SPEED_RAMP[index];
}

export function TrackMap({ data }: { data: TrackMapResponse }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const points = data.points;
  if (points.length < 2) return null;

  const size = 1000;
  const pad = 40;

  // The line is drawn as one segment per pair of samples, each carrying its own
  // colour. A single path could not vary along its length.
  const segments = points.slice(0, -1).map((point, i) => {
    const next = points[i + 1];
    return {
      key: i,
      d: `M${point.x.toFixed(1)},${point.y.toFixed(1)}L${next.x.toFixed(1)},${next.y.toFixed(1)}`,
      color: speedColor(point.speed_kmh, data.min_speed_kmh, data.max_speed_kmh),
      speed: point.speed_kmh,
      distance: point.distance_m,
    };
  });

  const cornerPoints = data.corners.map((corner) => {
    // Translate the corner's distance back onto the geometry.
    let closest = points[0];
    let best = Infinity;
    for (const point of points) {
      const gap = Math.abs(point.distance_m - corner.apex_distance_m);
      if (gap < best) {
        best = gap;
        closest = point;
      }
    }
    return { corner, x: closest.x, y: closest.y };
  });

  // The lap's last sample sits a few metres short of its first, so without an
  // explicit closing segment the circuit renders with a gap on the start/finish
  // straight — which reads as missing data rather than a closed loop.
  const first = points[0];
  const last = points[points.length - 1];
  segments.push({
    key: segments.length,
    d: `M${last.x.toFixed(1)},${last.y.toFixed(1)}L${first.x.toFixed(1)},${first.y.toFixed(1)}`,
    color: speedColor(last.speed_kmh, data.min_speed_kmh, data.max_speed_kmh),
    speed: last.speed_kmh,
    distance: last.distance_m,
  });

  const active = hovered !== null ? segments[hovered] : null;

  return (
    <div>
      <svg
        viewBox={`${-pad} ${-pad} ${size + pad * 2} ${size + pad * 2}`}
        className="mx-auto w-full max-w-lg"
        role="img"
        aria-label={`Track map for car ${data.driver_number} lap ${data.lap_number}, coloured by speed`}
        // The y-axis is flipped: positional data counts upward, SVG counts downward,
        // so without this the circuit renders mirrored.
        style={{ transform: "scaleY(-1)" }}
      >
        {/* A faint full-width line underneath keeps the circuit shape readable where
            the speed colour is at its lightest. */}
        <path
          d={
            points
              .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
              .join("") + "Z"
          }
          fill="none"
          stroke="var(--border)"
          strokeWidth={14}
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {segments.map((segment) => (
          <path
            key={segment.key}
            d={segment.d}
            fill="none"
            stroke={segment.color}
            strokeWidth={hovered === segment.key ? 14 : 9}
            strokeLinecap="round"
            onMouseEnter={() => setHovered(segment.key)}
            onMouseLeave={() => setHovered(null)}
            style={{ cursor: "crosshair" }}
          />
        ))}

        {cornerPoints.map(({ corner, x, y }) => (
          <g key={corner.index}>
            <circle
              cx={x}
              cy={y}
              r={20}
              fill="var(--surface-0)"
              stroke="var(--text-primary)"
              strokeWidth={2.5}
            />
            {/* Counter-flip the label, or the numbers render upside down. */}
            <text
              x={x}
              y={-y}
              transform="scale(1,-1)"
              textAnchor="middle"
              dominantBaseline="central"
              fontSize={22}
              fontWeight={600}
              fill="var(--text-primary)"
            >
              {corner.index}
            </text>
          </g>
        ))}
      </svg>

      {/* Sequential legend: the ramp with its endpoints labelled. */}
      <div className="mt-4 flex items-center gap-3">
        <span className="tnum text-xs" style={{ color: "var(--text-secondary)" }}>
          {data.min_speed_kmh.toFixed(0)}
        </span>
        <div className="flex h-2 flex-1 overflow-hidden rounded-sm">
          {SPEED_RAMP.map((color) => (
            <div key={color} className="flex-1" style={{ background: color }} />
          ))}
        </div>
        <span className="tnum text-xs" style={{ color: "var(--text-secondary)" }}>
          {data.max_speed_kmh.toFixed(0)} km/h
        </span>
      </div>

      <p className="mt-2 text-xs tnum" style={{ color: "var(--text-secondary)" }}>
        {active ? (
          <>
            {active.distance.toFixed(0)} m into the lap —{" "}
            <strong>{active.speed.toFixed(0)} km/h</strong>
          </>
        ) : (
          <>
            {data.lap_distance_m.toFixed(0)} m measured from the positional trace ·{" "}
            {data.corners.length} corners detected · hover the line for speed
          </>
        )}
      </p>
    </div>
  );
}
