"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { cn } from "./ui";

const MAIN = [["/", "Lead orders"], ["/settings", "Settings"]] as const;
const ADVANCED = [
  ["/today", "Outreach scorecard"], ["/hot-leads", "All hot leads"], ["/agencies", "Agencies"],
  ["/clients", "Clients"], ["/searches", "Searches"], ["/role-match", "Role Match"], ["/employers", "Employers"],
  ["/templates", "Templates"],
] as const;

const linkCls = (active: boolean) => cn("whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm no-underline hover:no-underline",
  active ? "bg-brand-soft font-medium text-brand dark:bg-blue-950 dark:text-blue-300"
         : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800");

export function Nav() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, []);
  const inAdvanced = ADVANCED.some(([href]) => path.startsWith(href));
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
      <div className="mx-auto flex max-w-7xl items-center gap-2 px-4 py-2">
        <Link href="/" className="shrink-0 font-semibold sm:mr-2 text-slate-900 no-underline hover:no-underline dark:text-white">
          Repost<span className="text-brand">Radar</span>
        </Link>
        <nav className="flex min-w-0 flex-1 items-center gap-0.5" aria-label="Main">
          {MAIN.map(([href, label]) => (
            <Link key={href} href={href} className={linkCls(href === "/" ? path === "/" : path.startsWith(href))}>{label}</Link>
          ))}
          <div className="relative" ref={box}>
            <button onClick={() => setOpen(!open)} aria-expanded={open} className={linkCls(inAdvanced)}>
              Advanced ▾
            </button>
            {open && (
              <div className="absolute right-0 top-full sm:left-0 sm:right-auto z-40 mt-1 w-52 rounded-lg border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-900">
                {ADVANCED.map(([href, label]) => (
                  <Link key={href} href={href} className={cn(linkCls(path.startsWith(href)), "block")}>{label}</Link>
                ))}
                <form action="/api/logout" method="post" className="border-t border-slate-100 pt-1 sm:hidden dark:border-slate-800">
                  <button className={cn(linkCls(false), "block w-full text-left")}>Log out</button>
                </form>
              </div>
            )}
          </div>
        </nav>
        <form action="/api/logout" method="post" className="hidden sm:block">
          <button className="shrink-0 text-xs text-slate-500 hover:text-slate-900 dark:hover:text-white">Log out</button>
        </form>
      </div>
    </header>
  );
}
