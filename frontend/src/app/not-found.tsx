/**
 * Not-found page.
 *
 * Next's stock 404 renders bare inside our shell — a number, a sentence, and no way
 * onward. A reader who mistyped a URL or followed a stale link needs to know which
 * parts of the application still exist, so this offers them rather than an apology.
 */

import Link from "next/link";

export default function NotFound() {
  return (
    <div className="py-8">
      <h1 className="text-lg font-semibold tracking-tight">Nothing at this address</h1>
      <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
        The page may have moved, or the link may have been mistyped.
      </p>

      <div
        className="mt-5 overflow-hidden rounded-lg border"
        style={{ borderColor: "var(--border)" }}
      >
        {[
          { href: "/", label: "Races", hint: "Every loaded race, with what happened in one line" },
          { href: "/season", label: "Season", hint: "Circuits by tyre demand, pace through a calendar" },
          { href: "/teammates", label: "Teammates", hint: "The comparison that removes the car" },
          { href: "/ratings", label: "Ratings", hint: "Pace, racecraft, consistency and tyre management" },
          { href: "/fetch", label: "Fetch", hint: "Pull in a race that is not loaded yet" },
        ].map((item, index) => (
          <Link
            key={item.href}
            href={item.href}
            className="flex items-baseline gap-3 px-4 py-2.5 transition-colors hover:bg-[var(--surface-2)]"
            style={{
              background: "var(--surface-1)",
              borderTop: index > 0 ? "1px solid var(--border)" : undefined,
            }}
          >
            <span className="w-24 shrink-0 text-sm font-medium">{item.label}</span>
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
              {item.hint}
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
