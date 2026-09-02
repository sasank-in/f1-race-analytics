/**
 * Telemetry comparison.
 *
 * Two laps, aligned by distance rather than time, with the cumulative delta showing
 * *where* the time went and a corner table showing what was different at each apex.
 *
 * This is a client component because the driver and lap selection is interactive —
 * the whole point is trying different pairings.
 */

"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";

import {
  api,
  type PaceResponse,
  type TelemetryCompareResponse,
  type TrackMapResponse,
} from "@/api/client";
import { DeltaTrace } from "@/components/charts";
import { TrackMap } from "@/components/track-map";
import { Card, Empty, ErrorNote } from "@/components/ui";

export default function TelemetryPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const sessionId = Number(id);

  const [pace, setPace] = useState<PaceResponse | null>(null);
  const [driverA, setDriverA] = useState("");
  const [driverB, setDriverB] = useState("");
  const [lap, setLap] = useState(20);
  const [result, setResult] = useState<TelemetryCompareResponse | null>(null);
  const [map, setMap] = useState<TrackMapResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Seed the selectors from the pace ranking, so the default comparison is the two
  // quickest cars rather than whichever numbers sort first.
  useEffect(() => {
    api
      .pace(sessionId)
      .then((data) => {
        setPace(data);
        if (data.drivers.length >= 2) {
          setDriverA(data.drivers[0].driver_number);
          setDriverB(data.drivers[1].driver_number);
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load drivers"));
  }, [sessionId]);

  useEffect(() => {
    if (!driverA || !driverB || driverA === driverB) return;
    setLoading(true);
    setError(null);
    api
      .telemetry(sessionId, driverA, lap, driverB, lap)
      .then(setResult)
      .catch((e) => {
        setResult(null);
        setError(e instanceof Error ? e.message : "Comparison failed");
      })
      .finally(() => setLoading(false));

    // The map is drawn for the reference lap, so it fails independently of the
    // comparison: a missing rival lap should not lose the circuit.
    api
      .trackMap(sessionId, driverA, lap)
      .then(setMap)
      .catch(() => setMap(null));
  }, [sessionId, driverA, driverB, lap]);

  const drivers = pace?.drivers ?? [];

  return (
    <div className="space-y-6">
      <div>
        <Link
          href={`/sessions/${sessionId}`}
          className="text-xs underline underline-offset-2"
          style={{ color: "var(--text-secondary)" }}
        >
          ← session
        </Link>
        <h1 className="mt-2 text-lg font-semibold tracking-tight">
          Telemetry comparison
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
          Two laps aligned by distance. Comparing by timestamp would compare unrelated
          corners.
        </p>
      </div>

      {/* Filters in one row above the charts. */}
      <div
        className="flex flex-wrap items-end gap-4 rounded-lg border p-4"
        style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}
      >
        <label className="text-xs">
          <span className="block pb-1" style={{ color: "var(--text-secondary)" }}>
            Reference
          </span>
          <select
            value={driverA}
            onChange={(e) => setDriverA(e.target.value)}
            className="tnum rounded border px-2 py-1 text-sm"
            style={{ background: "var(--surface-0)", borderColor: "var(--border-strong)" }}
          >
            {drivers.map((d) => (
              <option key={d.driver_number} value={d.driver_number}>
                #{d.driver_number} (P{d.rank})
              </option>
            ))}
          </select>
        </label>

        <label className="text-xs">
          <span className="block pb-1" style={{ color: "var(--text-secondary)" }}>
            Comparison
          </span>
          <select
            value={driverB}
            onChange={(e) => setDriverB(e.target.value)}
            className="tnum rounded border px-2 py-1 text-sm"
            style={{ background: "var(--surface-0)", borderColor: "var(--border-strong)" }}
          >
            {drivers.map((d) => (
              <option key={d.driver_number} value={d.driver_number}>
                #{d.driver_number} (P{d.rank})
              </option>
            ))}
          </select>
        </label>

        <label className="text-xs">
          <span className="block pb-1" style={{ color: "var(--text-secondary)" }}>
            Lap
          </span>
          <input
            type="number"
            min={1}
            value={lap}
            onChange={(e) => setLap(Math.max(1, Number(e.target.value)))}
            className="tnum w-20 rounded border px-2 py-1 text-sm"
            style={{ background: "var(--surface-0)", borderColor: "var(--border-strong)" }}
          />
        </label>

        {loading && (
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            loading…
          </span>
        )}
      </div>

      {error && <ErrorNote detail={error} />}

      {map && (
        <Card
          title="Track map"
          subtitle={`Car ${map.driver_number}, lap ${map.lap_number}, coloured by speed`}
          caveat="The circuit is drawn from the positional trace, not a stored map, so any layout with data renders. Numbered markers are the detected corners."
        >
          <TrackMap data={map} />
        </Card>
      )}

      {result && (
        <>
          <Card
            title="Cumulative delta"
            subtitle={`Car ${result.comparison_driver} against car ${result.reference_driver}, lap ${lap}`}
            caveat="Below the line the comparison lap is ahead. A rise on a straight after a corner means the time was won in the corner before it."
          >
            <div className="mb-4">
              <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
                Lap-time difference
              </span>
              <div
                className="tnum text-2xl font-semibold"
                style={{
                  color:
                    result.final_delta_s < 0 ? "var(--good)" : "var(--text-primary)",
                }}
              >
                {result.final_delta_s >= 0 ? "+" : ""}
                {result.final_delta_s.toFixed(3)}s
              </div>
            </div>
            <DeltaTrace
              distance={result.distance_m}
              delta={result.delta_s}
              referenceDriver={result.reference_driver}
              comparisonDriver={result.comparison_driver}
            />
          </Card>

          <Card
            title="Corner minimum speeds"
            subtitle={`${result.corners.length} corners detected from the speed trace`}
            caveat="Corners are found from local minima in smoothed speed, not a circuit map, so the engine works on any layout."
          >
            {result.corners.length === 0 ? (
              <Empty message="No corners matched between these laps." />
            ) : (
              <div className="space-y-2">
                {/* Column headers above the rows: the same labels underneath read as
                    a footnote, found only after the reader has guessed. */}
                <div
                  className="flex gap-3 border-b pb-1.5 text-xs"
                  style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
                >
                  <span className="w-6">turn</span>
                  <span className="w-16 text-right">apex</span>
                  <span className="w-14 text-right">#{result.reference_driver}</span>
                  <span className="w-14 text-right">#{result.comparison_driver}</span>
                  <span className="flex-1 text-center">
                    km/h difference at the apex
                  </span>
                  <span className="w-14 text-right">delta</span>
                </div>
                {result.corners.map((corner) => {
                  const faster = corner.delta_kmh > 0;
                  const magnitude = Math.min(
                    Math.abs(corner.delta_kmh) / 10,
                    1,
                  );
                  return (
                    <div
                      key={corner.index}
                      className="flex items-center gap-3 text-xs"
                    >
                      <span className="tnum w-6" style={{ color: "var(--text-muted)" }}>
                        T{corner.index}
                      </span>
                      <span
                        className="tnum w-16 text-right"
                        style={{ color: "var(--text-muted)" }}
                      >
                        {corner.apex_distance_m.toFixed(0)}m
                      </span>
                      <span className="tnum w-14 text-right">
                        {corner.reference_min_speed_kmh.toFixed(0)}
                      </span>
                      <span className="tnum w-14 text-right">
                        {corner.comparison_min_speed_kmh.toFixed(0)}
                      </span>

                      {/* Diverging bar: faster right, slower left, from a centre line. */}
                      <div className="relative h-3 flex-1">
                        <div
                          className="absolute inset-y-0"
                          style={{ left: "50%", width: 1, background: "var(--border-strong)" }}
                        />
                        <div
                          className="absolute h-3 rounded-sm"
                          style={{
                            left: faster ? "50%" : `${50 - magnitude * 50}%`,
                            width: `${magnitude * 50}%`,
                            background: faster ? "var(--series-3)" : "var(--series-2)",
                          }}
                        />
                      </div>

                      <span
                        className="tnum w-14 text-right font-medium"
                        style={{
                          color: faster ? "var(--series-3)" : "var(--series-2)",
                        }}
                      >
                        {corner.delta_kmh >= 0 ? "+" : ""}
                        {corner.delta_kmh.toFixed(1)}
                      </span>
                    </div>
                  );
                })}

              </div>
            )}
          </Card>
        </>
      )}

      {!result && !error && !loading && (
        <Empty message="Pick two drivers and a lap to compare." />
      )}
    </div>
  );
}
