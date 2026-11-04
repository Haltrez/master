"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, waitFor } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorNote, Input, Label, Select } from "./ui";

const MATCH_STATUSES = ["new", "sent", "contacted", "interview", "placed", "not_relevant"];

// Role Match for one agency: briefs (role profiles only, no names or CVs) and matches.
export function Candidates({ agencyId, briefs, matches, families }: { agencyId: number; briefs: any[]; matches: any[]; families: any[] }) {
  const router = useRouter();
  const [form, setForm] = useState({ role: families[0]?.name || "", location: "", radius_miles: 30, sector: "" });
  const [state, setState] = useState<string>();
  const [err, setErr] = useState<string>();
  const [rows, setRows] = useState(matches);

  async function addBrief(e: React.FormEvent) {
    e.preventDefault(); setErr(undefined);
    try {
      await api("/briefs", { body: { agency_id: agencyId, role: form.role, location: form.location,
        radius_miles: Number(form.radius_miles), sector_keywords: form.sector.split(",").map((s) => s.trim()).filter(Boolean) } });
      setForm({ ...form, location: "" });
      router.refresh();
    } catch (e: any) { setErr(e.message); }
  }
  async function toggle(b: any) { await api(`/briefs/${b.id}`, { method: "PATCH", body: { active: !b.active } }); router.refresh(); }
  async function find() {
    setState("Checking employers' careers pages and Reed… this can take a few minutes."); setErr(undefined);
    try {
      const { task_id } = await api(`/agencies/${agencyId}/match`, { method: "POST" });
      const t = await waitFor<any>(`/tasks/${task_id}`, (x) => x.status !== "running", 3000);
      if (t.status === "ok") {
        setState(`Done: ${t.result.matches} matches.`);
        setRows(await api(`/agencies/${agencyId}/matches`));
      } else { setState(undefined); setErr(t.error); }
    } catch (e: any) { setState(undefined); setErr(e.message); }
  }
  async function status(m: any, s: string) {
    await api("/matches/status", { body: { ids: [m.id], status: s } });
    setRows((r) => r.map((x) => (x.id === m.id ? { ...x, status: s } : x)));
  }
  async function toSheet() {
    const url = prompt("Google Sheet link for this agency (leave empty to use the client's sheet):") ?? null;
    if (url === null) return;
    try { await api(`/agencies/${agencyId}/matches/send-to-sheet${url ? `?sheet_url=${encodeURIComponent(url)}` : ""}`, { method: "POST" }); alert("Sent to Make.com: the sheet fills in a minute."); }
    catch (e: any) { alert(e.message); }
  }
  const grouped = briefs.map((b) => ({ b, ms: rows.filter((m) => m.brief_id === b.id) }));

  return (
    <div className="space-y-4">
      <Card title="Candidate briefs" action={<span className="text-xs text-slate-500">Role profiles only: never names or CVs</span>}>
        <form onSubmit={addBrief} className="grid gap-2 sm:grid-cols-5">
          <div className="sm:col-span-2"><Label>Role</Label><Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            {families.map((f) => <option key={f.id}>{f.name}</option>)}</Select></div>
          <div><Label>Location</Label><Input required value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} placeholder="Warrington" /></div>
          <div><Label>Radius (mi)</Label><Input type="number" min={1} value={form.radius_miles} onChange={(e) => setForm({ ...form, radius_miles: e.target.value as any })} /></div>
          <div><Label>Sector words</Label><Input value={form.sector} onChange={(e) => setForm({ ...form, sector: e.target.value })} placeholder="m&e, hvac" /></div>
          <div className="sm:col-span-5 flex justify-end"><Button>Add brief</Button></div>
        </form>
        <ErrorNote error={err} />
        {briefs.length > 0 && (
          <ul className="mt-3 divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {briefs.map((b) => (
              <li key={b.id} className="flex items-center justify-between gap-2 py-1.5">
                <span>{b.label} · {b.radius_miles} mi {b.sector_keywords?.length ? `· ${b.sector_keywords.join(", ")}` : ""} {b.lat == null && <Badge tone="amber">location not found</Badge>}</span>
                <Button size="sm" variant="ghost" onClick={() => toggle(b)}>{b.active ? "Pause" : "Activate"}</Button>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button onClick={find} disabled={!briefs.length || !!state?.endsWith("minutes.")} data-testid="find-matches">Find matches</Button>
          <a href={`/api/engine/exports/matches.csv?agency_id=${agencyId}`}><Button variant="secondary" type="button">Download CSV</Button></a>
          <Button variant="secondary" onClick={toSheet}>Send to Google Sheet</Button>
          {state && <span className="text-sm text-slate-600 dark:text-slate-300" role="status">{state}</span>}
        </div>
      </Card>
      {grouped.map(({ b, ms }) => (
        <Card key={b.id} title={b.label}>
          {ms.length === 0 ? <p className="text-sm text-slate-500">No matches yet.</p> : (
            <div className="overflow-x-auto"><table className="w-full min-w-[680px] text-sm">
              <thead className="text-left text-xs uppercase text-slate-500"><tr><th className="py-1">Company</th><th>Their role</th><th>Distance</th><th>Open since</th><th>Signal</th><th className="text-right">Score</th><th>Status</th></tr></thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {ms.map((m) => (
                  <tr key={m.id}>
                    <td className="py-1.5 font-medium">{m.company}</td>
                    <td><a href={(m.url || "").split("#")[0]} target="_blank" rel="noreferrer">{m.their_role}</a></td>
                    <td>{m.distance_miles != null ? `${Math.round(m.distance_miles)} mi` : "?"}</td>
                    <td>{m.open_since_label || "unknown"}</td>
                    <td className="text-slate-500" title={(m.reasons || []).join("\n")}>{m.signal}</td>
                    <td className="text-right font-semibold">{m.score}</td>
                    <td><select value={m.status} onChange={(e) => status(m, e.target.value)} className="rounded border border-slate-200 bg-transparent px-1 text-xs dark:border-slate-700" aria-label="Match status">
                      {MATCH_STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}</select></td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </Card>
      ))}
      {briefs.length === 0 && <Empty>Ask the agency which roles they have candidates for (no names needed), then add a brief per role.</Empty>}
    </div>
  );
}
