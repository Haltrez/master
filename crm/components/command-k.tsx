"use client";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

type Item = { href: string; label: string; hint: string };

export function CommandK() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [sel, setSel] = useState(0);
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
      if (e.key === "Escape") setOpen(false);
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("open-command", onOpen);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("open-command", onOpen); };
  }, []);

  useEffect(() => { if (open) setTimeout(() => input.current?.focus(), 10); else { setQ(""); setItems([]); } }, [open]);

  useEffect(() => {
    if (q.trim().length < 2) { setItems([]); return; }
    const t = setTimeout(async () => {
      try {
        const r = await api<any>(`/search?q=${encodeURIComponent(q.trim())}`);
        setItems([
          ...r.agencies.map((a: any) => ({ href: `/agencies/${a.id}`, label: a.name, hint: "Agency" })),
          ...r.contacts.map((c: any) => ({ href: `/agencies/${c.agency_id}`, label: c.full_name, hint: `Contact · ${c.agency_name}` })),
          ...r.hot_leads.map((h: any) => ({ href: `/hot-leads?q=${encodeURIComponent(h.employer_name)}`, label: `${h.employer_name}: ${h.title}`, hint: `Hot lead · score ${h.score}` })),
          ...r.employers.map((e: any) => ({ href: `/employers?q=${encodeURIComponent(e.name)}`, label: e.name, hint: "Employer" })),
        ]);
        setSel(0);
      } catch { setItems([]); }
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  if (!open) return null;
  const go = (i: Item) => { setOpen(false); router.push(i.href); };
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/40 p-4 pt-[12vh]" onClick={() => setOpen(false)}>
      <div className="w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-2xl dark:bg-slate-900" onClick={(e) => e.stopPropagation()}>
        <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search agencies, contacts, hot leads…"
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(items.length - 1, s + 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
            if (e.key === "Enter" && items[sel]) go(items[sel]);
          }}
          className="w-full border-b border-slate-200 bg-transparent px-4 py-3 outline-none dark:border-slate-800" aria-label="Search" />
        <ul className="max-h-80 overflow-y-auto py-1">
          {items.map((it, i) => (
            <li key={it.href + i}>
              <button onClick={() => go(it)} onMouseEnter={() => setSel(i)}
                className={`flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-sm ${i === sel ? "bg-brand-soft dark:bg-blue-950" : ""}`}>
                <span className="truncate">{it.label}</span><span className="shrink-0 text-xs text-slate-500">{it.hint}</span>
              </button>
            </li>
          ))}
          {q.trim().length >= 2 && items.length === 0 && <li className="px-4 py-3 text-sm text-slate-500">Nothing found.</li>}
        </ul>
      </div>
    </div>
  );
}
