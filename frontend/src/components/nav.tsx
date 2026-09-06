/**
 * Primary navigation.
 *
 * Split out of the layout as a client component because marking the current section
 * needs the pathname. Without it every link looks identical and the header stops
 * telling you where you are — on a site where four of the seven pages share a shell,
 * that is the difference between orientation and guesswork.
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Races" },
  { href: "/season", label: "Season" },
  { href: "/teammates", label: "Teammates" },
  { href: "/ratings", label: "Ratings" },
  { href: "/fetch", label: "Fetch" },
] as const;

export function Nav() {
  const pathname = usePathname();

  return (
    <nav className="flex gap-5 text-sm">
      {LINKS.map(({ href, label }) => {
        // Race pages live under /sessions, so the races tab stays lit while reading one.
        const active =
          href === "/"
            ? pathname === "/" || pathname.startsWith("/sessions")
            : pathname.startsWith(href);

        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={active ? "font-medium" : undefined}
            style={{
              color: active ? "var(--text-primary)" : "var(--text-secondary)",
            }}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
