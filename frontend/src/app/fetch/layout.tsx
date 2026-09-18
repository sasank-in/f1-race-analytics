/**
 * The fetch page is a client component and cannot export metadata itself, so its
 * title lives in a layout alongside it.
 */

export const metadata = { title: "Fetch a race" };

export default function FetchLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
