"use client";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Badge, Button, Empty, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { d } from "@/lib/format";

export function HotLeadsClient({ leads, searches, params }: { leads: any[]; searches: any[]; params: Record<string, string> }) {
  const router = useRouter();
  const [rows, setRows] = useState(leads);
  const [text, setText] = useState(params.q || "");
  const set = (k: string, v: string) => {
    const qs = new URLSearchParams(params as any);
    if (v) qs.set(k, v); else qs.delete(k);
    qs.delete("q");
    router.push(`/hot-leads?${qs}`);
  };
  async function status(key: string, s: string) {
    await api(`/hot-leads/${key}`, { method: "PATCH", body: { status: s } });
    setRows((r) => r.map((x) => (x.lead_key === key ? { ...x, status: s } : x)));
  }
  const shown = useMemo(() => rows.filter((r) => !text || `${r.employer_name} ${r.title}`.toLowerCase().includes(text.toLowerCase())), [rows, text]);

  return (
    <>
      <div className="mb-3 grid gap-2 sm:grid-cols-5">
        <Input placeholder="Filter by company or role" value={text} onChange={(e) => setText(e.target.value)} className="sm:col-span-2" />
        <Select value={params.search_id || ""} onChange={(e) => set("search_id", e.target.value)} aria-label="Search">
          <option value="">All searches</option>
          {searches.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </Select>
        <Select value={params.min_score || ""} onChange={(e) => set("min_score", e.target.value)} aria-label="Minimum score">
          <option value="">Any score</option>{[2, 4, 6, 8, 10].map((n) => <option key={n} value={n}>Score {n}+</option>)}
        </Select>
        <div className="flex gap-2">
          <Select value={params.status || ""} onChange={(e) => set("status", e.target.value)} aria-label="Status">
            <option value="">Any status</option><option value="new">New</option><option value="used">Used</option><option value="ignored">Ignored</option>
          </Select>
          <label className="flex shrink-0 items-center gap-1 text-sm"><input type="checkbox" checked={params.live === "1"} onChange={(e) => set("live", e.target.checked ? "1" : "")} /> Live</label>
        </div>
      </div>
      {shown.length === 0 ? <Empty>No hot leads match. They appear after searches run and a role gets reposted.</Empty> : (
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-800/50">
              <tr><th className="p-3">Employer</th><th className="p-3">Role</th><th className="p-3 text-right">Posted</th><th className="p-3">First seen</th>
                <th className="p-3">Last seen</th><th className="p-3">Location</th><th className="p-3 text-right">Score</th><th className="p-3">Status</th><th className="p-3"></th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {shown.map((h) => (
                <tr key={h.lead_key} className={h.status === "ignored" ? "opacity-50" : ""}>
                  <td className="p-3 font-medium">{h.employer_name}</td>
                  <td className="p-3">{h.title} {h.long_open && <Badge tone="amber" title="One ad open 30+ days">long open</Badge>}</td>
                  <td className="p-3 text-right tabular-nums">{h.posting_count_90d}×</td>
                  <td className="p-3">{d(h.first_seen_at)}</td>
                  <td className="p-3">{d(h.last_seen_at)} {h.live_now && <Badge tone="green">live</Badge>}</td>
                  <td className="p-3">{h.location_name}</td>
                  <td className="p-3 text-right font-semibold tabular-nums">{h.score}</td>
                  <td className="p-3"><Badge tone={h.status === "used" ? "blue" : "slate"}>{h.status}</Badge></td>
                  <td className="whitespace-nowrap p-3 text-right">
                    {h.latest_url && <a href={h.latest_url} target="_blank" rel="noreferrer" className="mr-2">Open ad</a>}
                    <Button size="sm" variant="ghost" onClick={() => status(h.lead_key, h.status === "used" ? "new" : "used")}>{h.status === "used" ? "Unmark" : "Mark used"}</Button>
                    <Button size="sm" variant="ghost" onClick={() => status(h.lead_key, h.status === "ignored" ? "new" : "ignored")}>{h.status === "ignored" ? "Restore" : "Ignore"}</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
