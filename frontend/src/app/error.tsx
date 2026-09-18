/**
 * Error boundary.
 *
 * Without one, an unexpected throw drops the reader onto Next's stock error screen,
 * outside the application shell and with no route back. Most failures here are the API
 * being unreachable or a session with nothing analysed, both recoverable — so the page
 * offers a retry before anything else.
 *
 * The message is shown rather than hidden. This is an analysis tool run by the person
 * who also runs its database; "something went wrong" would waste their time, and the
 * detail is usually the actionable part ("run f1x analyse first").
 */

"use client";

import { useEffect } from "react";
import Link from "next/link";

export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="py-8">
      <h1 className="text-lg font-semibold tracking-tight">This page did not load</h1>
      <p className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>
        Most often the API is not running, or this race has been ingested but not yet
        analysed.
      </p>

      <div
        className="mt-4 rounded-lg border p-4 text-sm"
        style={{ borderColor: "var(--critical)", background: "var(--surface-1)" }}
      >
        <span style={{ color: "var(--text-secondary)" }}>
          {error.message || "No detail was reported."}
        </span>
        {error.digest && (
          <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
            ({error.digest})
          </span>
        )}
      </div>

      <div className="mt-5 flex items-center gap-3">
        <button
          type="button"
          onClick={retry}
          className="rounded border px-3 py-1.5 text-xs font-medium transition-colors hover:bg-[var(--surface-2)]"
          style={{ borderColor: "var(--border-strong)", background: "var(--surface-1)" }}
        >
          Try again
        </button>
        <Link
          href="/"
          className="text-xs underline underline-offset-2"
          style={{ color: "var(--text-secondary)" }}
        >
          Back to races
        </Link>
      </div>

      <p className="mt-5 text-xs" style={{ color: "var(--text-muted)" }}>
        If the API is down, start it with{" "}
        <code>.venv/Scripts/python.exe -m f1x.cli api serve</code> — or run{" "}
        <code>start.bat</code>, which starts everything.
      </p>
    </div>
  );
}
