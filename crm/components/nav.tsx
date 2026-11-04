"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "./ui";

const LINKS = [
  ["/", "Today"], ["/hot-leads", "Hot leads"], ["/agencies", "Agencies"], ["/role-match", "Role Match"],
  ["/searches", "Searches"], ["/clients", "Clients"], ["/employers", "Employers"], ["/templates", "Templates"],
  ["/settings", "Settings"],
] as const;

export function Nav() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 sm:flex-nowrap">
        <Link href="/" className="shrink-0 font-semibold text-slate-900 no-underline hover:no-underline dark:text-white">
          Repost<span className="text-brand">Radar</span>
        </Link>
        <nav className="order-last -mx-1 flex w-full min-w-0 gap-0.5 overflow-x-auto px-1 sm:order-none sm:w-auto sm:flex-1" aria-label="Main">
          {LINKS.map(([href, label]) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href}
                className={cn("whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm no-underline hover:no-underline",
                  active ? "bg-brand-soft font-medium text-brand dark:bg-blue-950 dark:text-blue-300"
                         : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800")}>
                {label}
              </Link>
            );
          })}
        </nav>
        <button onClick={() => window.dispatchEvent(new Event("open-command"))}
          className="hidden shrink-0 rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-500 sm:block dark:border-slate-700">
          Search <kbd className="ml-1 font-sans">⌘K</kbd>
        </button>
        <form action="/api/logout" method="post" className="ml-auto sm:ml-0">
          <button className="shrink-0 text-xs text-slate-500 hover:text-slate-900 dark:hover:text-white">Log out</button>
        </form>
      </div>
    </header>
  );
}
