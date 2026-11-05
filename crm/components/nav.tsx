"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "./ui";

const MAIN = [["/", "Orders"], ["/projects", "Projects"], ["/outreach", "Outreach"], ["/leads", "Leads"], ["/agencies", "Agencies"], ["/settings", "Settings"]] as const;

const linkCls = (active: boolean) => cn("whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm no-underline hover:no-underline",
  active ? "bg-brand-soft font-medium text-brand dark:bg-blue-950 dark:text-blue-300"
         : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800");

export function Nav() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
      <div className="mx-auto flex max-w-7xl items-center gap-2 px-4 py-2">
        <Link href="/" className="hidden shrink-0 font-semibold sm:inline sm:mr-2 text-slate-900 no-underline hover:no-underline dark:text-white">
          Repost<span className="text-brand">Radar</span>
        </Link>
        <nav className="-mx-1 flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto px-1" aria-label="Main">
          {MAIN.map(([href, label]) => (
            <Link key={href} href={href} className={linkCls(href === "/" ? path === "/" : path.startsWith(href))}>{label}</Link>
          ))}
        </nav>
        <form action="/api/logout" method="post" className="hidden sm:block">
          <button className="shrink-0 text-xs text-slate-500 hover:text-slate-900 dark:hover:text-white">Log out</button>
        </form>
      </div>
    </header>
  );
}
