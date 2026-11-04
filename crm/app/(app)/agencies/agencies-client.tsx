"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useStageChange } from "@/components/stage-change";
import { Badge, Button, ComplianceBadge, Empty, Input, Select, cn } from "@/components/ui";
import { ago, STAGES, STAGE_LABEL } from "@/lib/format";

export function AgenciesClient({ agencies, searches, searchId }: { agencies: any[]; searches: any[]; searchId: string }) {
  const router = useRouter();
  const [rows, setRows] = useState(agencies);
  const [view, setView] = useState<"board" | "table">("board");
  const [q, setQ] = useState("");
  const [over, setOver] = useState<string | null>(null);
  const { change, dialog } = useStageChange((id, stage) => { setRows((r) => r.map((a) => (a.id === id ? { ...a, stage } : a))); router.refresh(); });
  const shown = useMemo(() => rows.filter((a) => !q || a.name.toLowerCase().includes(q.toLowerCase())), [rows, q]);

  return (
    <>
      <div className="mb-3 flex flex-wrap gap-2">
        <Input placeholder="Filter agencies" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
        <Select value={searchId} onChange={(e) => router.push(e.target.value ? `/agencies?search_id=${e.target.value}` : "/agencies")} className="max-w-xs" aria-label="Niche">
          <option value="">All niches</option>{searches.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </Select>
        <div className="ml-auto flex rounded-lg border border-slate-300 p-0.5 dark:border-slate-700">
          {(["board", "table"] as const).map((v) => <Button key={v} size="sm" variant={view === v ? "primary" : "ghost"} onClick={() => setView(v)}>{v === "board" ? "Pipeline" : "Table"}</Button>)}
        </div>
      </div>
      {rows.length === 0 ? <Empty action={<Link href="/searches">Go to searches</Link>}>No agencies yet. They appear once your searches collect agency ads.</Empty>
        : view === "board" ? (
        <div className="-mx-4 flex snap-x gap-3 overflow-x-auto px-4 pb-4">
          {STAGES.map((st) => {
            const col = shown.filter((a) => a.stage === st);
            return (
              <div key={st} data-stage={st}
                onDragOver={(e) => { e.preventDefault(); setOver(st); }} onDragLeave={() => setOver(null)}
                onDrop={(e) => { e.preventDefault(); setOver(null); const a = rows.find((x) => String(x.id) === e.dataTransfer.getData("text/plain")); if (a) change(a, st); }}
                className={cn("w-72 shrink-0 snap-start rounded-2xl bg-slate-100 p-2 dark:bg-slate-900", over === st && "ring-2 ring-brand")}>
                <h3 className="mb-2 flex items-center justify-between px-1 text-sm font-semibold">{STAGE_LABEL[st]} <Badge>{col.length}</Badge></h3>
                <div className="space-y-2">
                  {col.slice(0, 200).map((a) => (
                    <div key={a.id} draggable onDragStart={(e) => e.dataTransfer.setData("text/plain", String(a.id))}
                      className="cursor-grab rounded-xl border border-slate-200 bg-white p-3 shadow-sm active:cursor-grabbing dark:border-slate-700 dark:bg-slate-800" data-testid={`card-${a.id}`}>
                      <div className="flex items-start justify-between gap-2">
                        <Link href={`/agencies/${a.id}`} className="font-medium">{a.name}</Link><ComplianceBadge agency={a} />
                      </div>
                      <p className="mt-1 truncate text-xs text-slate-500">{a.niches || "no niche"} · {a.ads_count_90d} {a.ads_count_90d === 1 ? "ad" : "ads"} / 90d</p>
                      <p className="truncate text-xs text-slate-500">{a.director_name || "no director yet"}</p>
                      {/* stage picker for phones, where drag isn't practical */}
                      <select aria-label={`Move ${a.name}`} value={a.stage} onChange={(e) => change(a, e.target.value)}
                        className="mt-2 w-full rounded-md border border-slate-200 bg-transparent px-1 py-0.5 text-xs dark:border-slate-700 sm:hidden">
                        {STAGES.map((s) => <option key={s} value={s}>{STAGE_LABEL[s]}</option>)}
                      </select>
                    </div>
                  ))}
                  {col.length > 200 && <p className="px-1 text-xs text-slate-500">+{col.length - 200} more, use the table view</p>}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-800/50">
              <tr><th className="p-3">Agency</th><th className="p-3">Niche</th><th className="p-3 text-right">Ads 90d</th><th className="p-3">Director</th><th className="p-3">Company</th><th className="p-3">Stage</th><th className="p-3">Last activity</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {shown.map((a) => (
                <tr key={a.id}>
                  <td className="p-3"><Link href={`/agencies/${a.id}`} className="font-medium">{a.name}</Link></td>
                  <td className="p-3 text-slate-500">{a.niches}</td><td className="p-3 text-right">{a.ads_count_90d}</td>
                  <td className="p-3">{a.director_name || "–"}</td><td className="p-3"><ComplianceBadge agency={a} /></td>
                  <td className="p-3"><select value={a.stage} onChange={(e) => change(a, e.target.value)} className="rounded border border-slate-200 bg-transparent px-1 py-0.5 dark:border-slate-700" aria-label={`Stage of ${a.name}`}>
                    {STAGES.map((s) => <option key={s} value={s}>{STAGE_LABEL[s]}</option>)}</select></td>
                  <td className="p-3 text-slate-500">{ago(a.last_activity_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog}
    </>
  );
}
