"use client";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Badge, Button, Empty, Input, Select } from "@/components/ui";
import { api, waitFor } from "@/lib/api";
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
  const [finding, setFinding] = useState<Record<string, string>>({});
  const filters = new URLSearchParams(Object.entries({
    search_id: params.search_id || "", min_score: params.min_score || "", status: params.status || "",
    live_only: params.live === "1" ? "true" : "" }).filter(([, v]) => v)).toString();
  const csvHref = `/api/engine/exports/hot-leads.csv?${filters}`;
  async function findContact(key: string) {
    setFinding((f) => ({ ...f, [key]: "looking…" }));
    try {
      const { task_id } = await api(`/hot-leads/${key}/contact`, { method: "POST" });
      const t = await waitFor<any>(`/tasks/${task_id}`, (x) => x.status !== "running", 2000);
      if (t.status !== "ok") { setFinding((f) => ({ ...f, [key]: `error: ${t.error}` })); return; }
      setRows(await api(`/hot-leads?${filters}`));
      setFinding((f) => ({ ...f, [key]: "" }));
    } catch (e: any) { setFinding((f) => ({ ...f, [key]: e.message })); }
  }
  const shown = useMemo(() => rows.filter((r) => !text || `${r.employer_name} ${r.title}`.toLowerCase().includes(text.toLowerCase())), [rows, text]);

  return (
    <>
      <div className="mb-3 flex justify-end"><a href={csvHref} className="no-underline"><Button variant="secondary" type="button">Download CSV</Button></a></div>
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
          <table className="w-full min-w-[920px] text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-800/50">
              <tr><th className="p-3">Employer</th><th className="p-3">Role</th><th className="p-3 text-right">Posted</th><th className="p-3">First seen</th>
                <th className="p-3">Last seen</th><th className="p-3">Location</th><th className="p-3">Decision maker</th><th className="p-3 text-right">Score</th><th className="p-3">Status</th><th className="p-3"></th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {shown.map((h) => (
                <tr key={h.lead_key} className={h.status === "ignored" ? "opacity-50" : ""}>
                  <td className="p-3 font-medium">{h.employer_name}
                    <span className="block">{h.company_verified ? <Badge tone="green" title="Active company on Companies House">verified</Badge>
                      : <Badge tone="amber" title="Not in client sheets until Companies House confirms it's a real company">{h.company_check || "not checked"}</Badge>}</span></td>
                  <td className="p-3">{h.title} {h.long_open && <Badge tone="amber" title="One ad open 30+ days">long open</Badge>}</td>
                  <td className="p-3 text-right tabular-nums">{h.posting_count_90d}×</td>
                  <td className="p-3">{d(h.first_seen_at)}</td>
                  <td className="p-3">{d(h.last_seen_at)} {h.live_now && <Badge tone="green">live</Badge>}</td>
                  <td className="p-3">{h.location_name}</td>
                  <td className="p-3">{h.contact?.name ? (
                    <span>{h.contact.name}{h.contact.role && <span className="block text-xs text-slate-500">{h.contact.role}</span>}
                      {h.contact.email && <span className="block text-xs">{h.contact.email} {h.contact.verified ? <Badge tone="green">verified</Badge> : <Badge tone="amber">unverified</Badge>}</span>}</span>
                  ) : finding[h.lead_key] ? <span className="text-xs text-slate-500">{finding[h.lead_key]}</span>
                    : <Button size="sm" variant="ghost" onClick={() => findContact(h.lead_key)}>{h.contact?.checked ? "Retry" : "Find contact"}</Button>}</td>
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
