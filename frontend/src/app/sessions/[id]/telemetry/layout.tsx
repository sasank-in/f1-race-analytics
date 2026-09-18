/**
 * The telemetry page is interactive throughout, so it is a client component and its
 * title lives here. Static rather than the race name: a client page cannot await the
 * session, and "Telemetry" is already distinct from the sibling tabs.
 */

export const metadata = { title: "Telemetry" };

export default function TelemetryLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
