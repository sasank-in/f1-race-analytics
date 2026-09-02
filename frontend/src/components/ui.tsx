/**
 * Shared presentation pieces.
 *
 * Two conventions run through all of them:
 *
 * Numbers wear tabular figures, so a column of lap times aligns on the decimal point
 * rather than drifting. And an estimate never appears without its caveat — the engine
 * spent several phases establishing which numbers are modelled rather than measured,
 * and that distinction has to survive the trip to the screen.
 */

import type { ReactNode } from "react";

export function Card({
  title,
  subtitle,
  caveat,
  children,
}: {
  title: string;
  subtitle?: string;
  /** Shown in muted text under the title. Use it whenever the value is modelled. */
  caveat?: string;
  children: ReactNode;
}) {
  return (
    <section
      className="rounded-lg border p-5"
      style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}
    >
      <header className="mb-4">
        <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
        {subtitle && (
          <p className="mt-0.5 text-xs" style={{ color: "var(--text-secondary)" }}>
            {subtitle}
          </p>
        )}
        {caveat && (
          <p className="mt-1.5 text-xs italic" style={{ color: "var(--text-muted)" }}>
            {caveat}
          </p>
        )}
      </header>
      {children}
    </section>
  );
}

/** A single headline number. Used where a chart would be overkill for one value. */
export function Stat({
  label,
  value,
  unit,
  hint,
}: {
  label: string;
  value: string;
  unit?: string;
  hint?: string;
}) {
  return (
    <div>
      <div className="text-xs" style={{ color: "var(--text-secondary)" }}>
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="tnum text-2xl font-semibold">{value}</span>
        {unit && (
          <span className="text-sm" style={{ color: "var(--text-secondary)" }}>
            {unit}
          </span>
        )}
      </div>
      {hint && (
        <div className="mt-0.5 text-xs" style={{ color: "var(--text-muted)" }}>
          {hint}
        </div>
      )}
    </div>
  );
}

export function Empty({ message }: { message: string }) {
  return (
    <div
      className="rounded-lg border border-dashed p-8 text-center text-sm"
      style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
    >
      {message}
    </div>
  );
}

/**
 * An error the backend explained. The API returns actionable details — "run
 * f1x analyse first" — so the UI shows that rather than a generic failure.
 */
export function ErrorNote({ detail }: { detail: string }) {
  return (
    <div
      className="rounded-lg border p-4 text-sm"
      style={{
        borderColor: "var(--critical)",
        background: "var(--surface-1)",
        color: "var(--text-primary)",
      }}
    >
      <span className="font-medium">Could not load. </span>
      <span style={{ color: "var(--text-secondary)" }}>{detail}</span>
    </div>
  );
}

const COMPOUND_TOKENS: Record<string, string> = {
  SOFT: "var(--compound-soft)",
  MEDIUM: "var(--compound-medium)",
  HARD: "var(--compound-hard)",
  INTERMEDIATE: "var(--compound-intermediate)",
  WET: "var(--compound-wet)",
};

export function compoundColor(compound: string | null | undefined): string {
  return COMPOUND_TOKENS[compound ?? ""] ?? "var(--text-muted)";
}

/**
 * Compound identity, always with the name spelled out.
 *
 * The colours are F1's own, which means HARD is grey and fails a chroma floor.
 * Pairing every swatch with its label keeps identity off colour alone.
 */
export function CompoundTag({ compound }: { compound: string | null | undefined }) {
  if (!compound) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className="inline-block h-2.5 w-2.5 rounded-full"
        style={{
          background: compoundColor(compound),
          // A ring keeps the grey HARD swatch visible on either surface.
          boxShadow: "0 0 0 1px var(--border-strong)",
        }}
      />
      <span className="text-xs">{compound}</span>
    </span>
  );
}

/** Legend for a multi-series chart. Always present when more than one series shows. */
export function Legend({
  items,
}: {
  items: { label: string; color: string }[];
}) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5 text-xs">
          <span
            aria-hidden
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ background: item.color }}
          />
          <span style={{ color: "var(--text-secondary)" }}>{item.label}</span>
        </li>
      ))}
    </ul>
  );
}
