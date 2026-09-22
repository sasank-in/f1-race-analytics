import type { Metadata } from "next";
import Link from "next/link";
import { Nav } from "@/components/nav";
import "./globals.css";

export const metadata: Metadata = {
  // A template rather than a fixed string: with eight pages, tabs that all read
  // "F1 Race Analysis Engine" are indistinguishable once more than one is open.
  title: {
    default: "F1 Race Analysis Engine",
    template: "%s · F1 Race Analysis Engine",
  },
  description:
    "Fuel-corrected pace, tyre degradation, strategy and telemetry analysis",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="mx-auto max-w-6xl px-6 py-8">
          {/* Stacks below sm and sits on one row above it. Forcing both onto a
              single row at every width wrapped the wordmark to three lines on a
              phone and pushed the last nav item off the screen. */}
          <header
            className="mb-8 flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4"
            style={{ borderColor: "var(--border)" }}
          >
            <Link
              href="/"
              className="shrink-0 text-base font-semibold tracking-tight"
            >
              F1 Race Analysis Engine
            </Link>
            <Nav />
          </header>
          {children}
          <footer
            className="mt-12 border-t pt-4 text-xs"
            style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
          >
            Pace, degradation and strategy figures are modelled from timing data, not
            measured from team telemetry. Each response carries the engine version that
            produced it.
          </footer>
        </div>
      </body>
    </html>
  );
}
