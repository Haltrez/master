"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { d, gbp } from "@/lib/format";

const PLAN = { pilot: "Pilot", founding: "Founding", done_for_you: "Done for you" } as Record<string, string>;

export function ClientsClient({ clients, searches }: { clients: any[]; searches: any[] }) {
  const router = useRouter();
  const [editing, setEditing] = useState<number | null>(null);
  const [picked, setPicked] = useState<number[]>([]);
  const [sheet, setSheet] = useState("");
  const [err, setErr] = useState<string>();
  if (!clients.length) return <Empty action={<Link href="/agencies">Go to agencies</Link>}>No clients yet. Move an agency to Pilot to start one.</Empty>;

  async function saveTerritories(id: number) {
    setErr(undefined);
    try { await api(`/clients/${id}/territories`, { method: "PUT", body: { search_ids: picked } });
      if (sheet) await api(`/clients/${id}/sheet`, { method: "PATCH", body: { sheet_url: sheet } });
      setEditing(null); router.refresh(); } catch (e: any) { setErr(e.message); }
  }
  async function convert(id: number) {
    if (!confirm("Convert this pilot to the paid done-for-you plan at £1,250/month?")) return;
    await api(`/clients/${id}/convert`, { method: "POST" }); router.refresh();
  }
  async function setStatus(id: number, status: string) { await api(`/clients/${id}`, { method: "PATCH", body: { status } }); router.refresh(); }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      {clients.map((c) => (
        <Card key={c.id} title={<span className="flex flex-wrap items-center gap-2"><Link href={`/agencies/${c.agency_id}`}>{c.agency_name}</Link>
          <Badge tone={c.plan === "pilot" ? "amber" : "green"}>{PLAN[c.plan]}</Badge>{c.status !== "active" && <Badge>{c.status}</Badge>}</span>}>
          <p className="text-sm text-slate-500">{gbp(c.monthly_price_gbp)}/month · since {d(c.start_date)}{c.end_date ? ` · ends ${d(c.end_date)}` : ""}</p>
          <p className="mt-2 text-sm">Territories: {c.territories.length ? c.territories.map((t: any) => t.label).join(", ") : <span className="text-amber-700">none yet</span>}</p>
          <p className="text-sm">Sheet: {c.sheet_url ? <a href={c.sheet_url} target="_blank" rel="noreferrer">open Google Sheet</a> : <span className="text-slate-500">not created yet</span>}</p>
          <dl className="mt-3 grid grid-cols-4 gap-2 text-center text-sm">
            {[["Delivered", c.leads_delivered], ["Contacted", c.contacted], ["Meetings", c.meetings], ["Placements", c.placements]].map(([k, v]) => (
              <div key={k as string}><dt className="text-xs text-slate-500">{k}</dt><dd className="font-semibold">{v}</dd></div>))}
          </dl>
          {editing === c.id ? (
            <div className="mt-3 space-y-2">
              <div className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-slate-200 p-2 dark:border-slate-700">
                {searches.map((s) => {
                  const owned = s.owner && s.owner.client_id !== c.id;
                  return <label key={s.id} className={`flex items-center gap-2 text-sm ${owned ? "text-slate-400" : ""}`}>
                    <input type="checkbox" disabled={!!owned} checked={picked.includes(s.id)} onChange={(e) => setPicked((p) => e.target.checked ? [...p, s.id] : p.filter((x) => x !== s.id))} />
                    {s.label}{owned && ` (owned by ${s.owner.agency})`}</label>;
                })}
              </div>
              <Input placeholder="Google Sheet link" value={sheet} onChange={(e) => setSheet(e.target.value)} />
              <ErrorNote error={err} />
              <div className="flex gap-2"><Button size="sm" onClick={() => saveTerritories(c.id)}>Save</Button><Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button></div>
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" variant="secondary" onClick={() => { setEditing(c.id); setPicked(c.territories.map((t: any) => t.search_id)); setSheet(c.sheet_url || ""); }}>Territories & sheet</Button>
              {c.plan === "pilot" && <Button size="sm" onClick={() => convert(c.id)}>Convert pilot to paid</Button>}
              {c.status === "active" ? <Button size="sm" variant="ghost" onClick={() => setStatus(c.id, "paused")}>Pause</Button>
                : <Button size="sm" variant="ghost" onClick={() => setStatus(c.id, "active")}>Resume</Button>}
              <Button size="sm" variant="ghost" onClick={() => setStatus(c.id, "ended")}>End</Button>
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}
