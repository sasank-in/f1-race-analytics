import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "F1 Race Analysis Engine",
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
          <header className="mb-8 flex items-baseline justify-between border-b pb-4"
            style={{ borderColor: "var(--border)" }}>
            <Link href="/" className="text-base font-semibold tracking-tight">
              F1 Race Analysis Engine
            </Link>
            <nav className="flex gap-5 text-sm">
              <Link href="/" style={{ color: "var(--text-secondary)" }}>
                Sessions
              </Link>
              <Link href="/ratings" style={{ color: "var(--text-secondary)" }}>
                Ratings
              </Link>
            </nav>
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
