"use client";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Badge, Button, Card, Empty, Input, Select } from "@/components/ui";
import { api, waitFor } from "@/lib/api";
import { ago } from "@/lib/format";

const TONE: Record<string, any> = { active: "green", research: "blue", needs_manual: "amber", blocked_by_robots: "red", ignore: "slate" };

export function EmployersClient({ employers, initialQ }: { employers: any[]; initialQ: string }) {
  const router = useRouter();
  const [rows, setRows] = useState(employers);
  const [status, setStatus] = useState("");
  const [q, setQ] = useState(initialQ);
  const [town, setTown] = useState("");
  const [urls, setUrls] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<Record<number, string>>({});
  const [add, setAdd] = useState({ name: "", careers_url: "", town: "" });
  const shown = useMemo(() => rows.filter((e) => (!status || e.status === status) && (!q || e.name.toLowerCase().includes(q.toLowerCase()))
    && (!town || (e.town || "").toLowerCase().includes(town.toLowerCase()))), [rows, status, q, town]);

  async function check(e: any) {
    setBusy((b) => ({ ...b, [e.id]: "checking…" }));
    const { task_id } = await api(`/employers/${e.id}/check`, { method: "POST" });
    const t = await waitFor<any>(`/tasks/${task_id}`, (x) => x.status !== "running", 2000);
    setBusy((b) => ({ ...b, [e.id]: t.status === "ok" ? `${t.result.outcome}: ${t.result.jobs} roles ${t.result.note || ""}` : `error: ${t.error}` }));
    setRows(await api("/employers"));
  }
  async function saveUrl(e: any) {
    const careers_url = urls[e.id];
    if (!careers_url) return;
    const r = await api(`/employers/${e.id}`, { method: "PATCH", body: { careers_url } });
    setRows((x) => x.map((y) => (y.id === e.id ? { ...y, ...r } : y)));
    await check({ ...e, careers_url });
  }
  async function create(ev: React.FormEvent) {
    ev.preventDefault();
    await api("/employers", { body: { name: add.name, careers_url: add.careers_url || null, town: add.town || null } });
    setAdd({ name: "", careers_url: "", town: "" }); setRows(await api("/employers")); router.refresh();
  }
  async function ignore(e: any) { await api(`/employers/${e.id}`, { method: "PATCH", body: { status: "ignore" } }); setRows(await api("/employers")); }

  return (
    <div className="space-y-4">
      <Card title="Add employer">
        <form onSubmit={create} className="grid gap-2 sm:grid-cols-4">
          <Input required placeholder="Company name" value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} />
          <Input placeholder="Careers page URL" value={add.careers_url} onChange={(e) => setAdd({ ...add, careers_url: e.target.value })} className="sm:col-span-2" />
          <div className="flex gap-2"><Input placeholder="Town" value={add.town} onChange={(e) => setAdd({ ...add, town: e.target.value })} /><Button>Add</Button></div>
        </form>
      </Card>
      <div className="grid gap-2 sm:grid-cols-3">
        <Input placeholder="Filter by name" value={q} onChange={(e) => setQ(e.target.value)} />
        <Input placeholder="Town" value={town} onChange={(e) => setTown(e.target.value)} />
        <Select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status"><option value="">All statuses</option>
          {Object.keys(TONE).map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}</Select>
      </div>
      {shown.length === 0 ? <Empty>No employers match.</Empty> : (
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-800/50">
              <tr><th className="p-3">Employer</th><th className="p-3">Town</th><th className="p-3">Careers page</th><th className="p-3">Status</th><th className="p-3 text-right">Roles</th><th className="p-3">Checked</th><th className="p-3"></th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {shown.slice(0, 500).map((e) => (
                <tr key={e.id}>
                  <td className="p-3 font-medium">{e.name}<span className="block text-xs font-normal text-slate-500">{e.source}</span></td>
                  <td className="p-3">{e.town}</td>
                  <td className="p-3">{e.careers_url ? <a href={e.careers_url} target="_blank" rel="noreferrer" className="break-all">{e.careers_url.replace(/^https?:\/\//, "").slice(0, 40)}</a> : (
                    <div className="flex gap-1"><Input placeholder="add careers URL" value={urls[e.id] || ""} onChange={(x) => setUrls({ ...urls, [e.id]: x.target.value })} className="py-1" />
                      <Button size="sm" onClick={() => saveUrl(e)}>Save & check</Button></div>)}</td>
                  <td className="p-3"><Badge tone={TONE[e.status]}>{e.status.replace(/_/g, " ")}</Badge></td>
                  <td className="p-3 text-right">{e.open_roles}</td>
                  <td className="p-3 text-slate-500">{ago(e.last_checked_at)}{busy[e.id] && <span className="block text-xs">{busy[e.id]}</span>}</td>
                  <td className="whitespace-nowrap p-3 text-right">{e.careers_url && <Button size="sm" variant="ghost" onClick={() => check(e)}>Check now</Button>}
                    {e.status !== "ignore" && <Button size="sm" variant="ghost" onClick={() => ignore(e)}>Ignore</Button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
