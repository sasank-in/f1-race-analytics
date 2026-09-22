/**
 * Sector strengths.
 *
 * The pace ranking says which car was quickest. This says *where* the time came from,
 * which is a different claim: two cars a tenth apart on a lap can be built completely
 * differently, one strong through the slow technical section and the other carrying
 * speed through the fast sweepers.
 *
 * Each sector is drawn as a bar of the gap to the benchmark in that sector, on a shared
 * scale so the three are comparable within a row and across rows. The benchmark in each
 * sector is set independently, so a driver can own one sector and be nowhere in another
 * — which is exactly the case this view exists to show.
 */

"use client";

import type { SectorProfile } from "@/api/client";

/** One colour per sector. Position in the lap is the identity, not the driver. */
const SECTOR_COLOURS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
] as const;

export function SectorChart({ drivers }: { drivers: SectorProfile[] }) {
  if (drivers.length === 0) return null;

  // A shared scale across every bar: a 0.3s gap must look the same wherever it
  // appears, or the rows cannot be compared with each other.
  const worst = Math.max(
    ...drivers.flatMap((d) => [d.gap1_s, d.gap2_s, d.gap3_s]),
    0.05,
  );

  return (
    <div>
      <div
        className="mb-2 flex items-center gap-3 border-b pb-1.5 text-xs"
        style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
      >
        <span className="w-12">driver</span>
        <span className="flex-1">gap to the quickest car in each sector</span>
        <span className="w-16 text-right">spread</span>
      </div>

      <div className="space-y-2">
        {drivers.map((driver) => {
          const gaps = [driver.gap1_s, driver.gap2_s, driver.gap3_s];
          return (
            <div key={driver.driver_number} className="flex items-center gap-3 text-xs">
              <span className="w-12 shrink-0 font-medium">
                {driver.abbreviation ?? `#${driver.driver_number}`}
              </span>

              <div className="flex flex-1 gap-1">
                {gaps.map((gap, index) => (
                  <div key={index} className="flex-1">
                    <div
                      className="relative h-4 rounded-sm"
                      style={{ background: "var(--surface-2)" }}
                      title={`S${index + 1}: ${gap === 0 ? "quickest" : `+${gap.toFixed(3)}s`}`}
                    >
                      <div
                        className="h-4 rounded-sm"
                        style={{
                          width: `${Math.max((gap / worst) * 100, gap === 0 ? 0 : 2)}%`,
                          background: SECTOR_COLOURS[index],
                        }}
                      />
                    </div>
                    <div
                      className="tnum mt-0.5 text-center text-[10px]"
                      style={{
                        color:
                          gap === 0 ? "var(--good)" : "var(--text-muted)",
                      }}
                    >
                      {gap === 0 ? "best" : `+${gap.toFixed(2)}`}
                    </div>
                  </div>
                ))}
              </div>

              <span
                className="tnum w-16 shrink-0 text-right"
                style={{ color: "var(--text-secondary)" }}
                title="Difference between this driver's largest and smallest sector gap"
              >
                {driver.spread_s.toFixed(3)}
              </span>
            </div>
          );
        })}
      </div>

      <div
        className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs"
        style={{ color: "var(--text-muted)" }}
      >
        {SECTOR_COLOURS.map((colour, index) => (
          <span key={index} className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ background: colour }}
            />
            sector {index + 1}
          </span>
        ))}
      </div>

      <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
        A car slower everywhere has a small spread. A large spread is a car with a
        specific weakness — and that is the one worth asking about.
      </p>
    </div>
  );
}
