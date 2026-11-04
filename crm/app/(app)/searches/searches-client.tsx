"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { TagInput } from "@/components/tag-input";
import { Badge, Button, Card, Empty, ErrorNote, Input, Label } from "@/components/ui";
import { api, waitFor } from "@/lib/api";
import { ago } from "@/lib/format";

type S = any;
const blank = { label: "", keywords: [] as string[], location: "", radius_miles: 15, min_salary: "" as any, active: true };

export function SearchesClient({ searches }: { searches: S[] }) {
  const router = useRouter();
  const [editing, setEditing] = useState<S | null>(searches.length ? null : { ...blank });
  const [running, setRunning] = useState<Record<number, string>>({});
  const [err, setErr] = useState<string>();

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setErr(undefined);
    const body = { label: editing.label, keywords: editing.keywords, location: editing.location,
      radius_miles: Number(editing.radius_miles) || 15, min_salary: editing.min_salary ? Number(editing.min_salary) : null,
      active: editing.active };
    try {
      if (editing.id) await api(`/searches/${editing.id}`, { method: "PATCH", body });
      else await api("/searches", { body });
      setEditing(null);
      router.refresh();
    } catch (e: any) { setErr(e.message); }
  }

  async function runNow(id: number) {
    setRunning((r) => ({ ...r, [id]: "starting…" }));
    try {
      const { run_id } = await api(`/searches/${id}/run`, { method: "POST" });
      setRunning((r) => ({ ...r, [id]: "collecting…" }));
      const run = await waitFor<any>(`/runs/${run_id}`, (x) => x.status !== "running", 2000);
      setRunning((r) => ({ ...r, [id]: run.status === "ok" ? `done: ${run.listings_seen} ads, ${run.new_listings} new`
        : `error: ${run.error}` }));
      router.refresh();
    } catch (e: any) { setRunning((r) => ({ ...r, [id]: `error: ${e.message}` })); }
  }

  return (
    <div className="space-y-4">
      {!editing && <Button onClick={() => setEditing({ ...blank })} data-testid="add-search">Add search</Button>}
      {editing && (
        <Card title={editing.id ? `Edit ${editing.label}` : "New search"}>
          <form onSubmit={save} className="grid gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2"><Label htmlFor="label">Label</Label>
              <Input id="label" required value={editing.label} placeholder="Accountancy - Leeds"
                onChange={(e) => setEditing({ ...editing, label: e.target.value })} /></div>
            <div className="sm:col-span-2"><Label>Keywords (each searched separately)</Label>
              <TagInput value={editing.keywords} onChange={(k) => setEditing({ ...editing, keywords: k })}
                placeholder="management accountant, finance manager…" /></div>
            <div><Label htmlFor="loc">Location</Label>
              <Input id="loc" required value={editing.location} onChange={(e) => setEditing({ ...editing, location: e.target.value })} placeholder="Leeds" /></div>
            <div className="grid grid-cols-2 gap-3">
              <div><Label htmlFor="radius">Radius (miles)</Label>
                <Input id="radius" type="number" min={1} value={editing.radius_miles} onChange={(e) => setEditing({ ...editing, radius_miles: e.target.value })} /></div>
              <div><Label htmlFor="minsal">Min salary £</Label>
                <Input id="minsal" type="number" min={0} value={editing.min_salary ?? ""} onChange={(e) => setEditing({ ...editing, min_salary: e.target.value })} /></div>
            </div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={editing.active}
              onChange={(e) => setEditing({ ...editing, active: e.target.checked })} /> Active (runs daily)</label>
            <div className="flex justify-end gap-2 sm:col-span-2">
              <ErrorNote error={err} />
              {searches.length > 0 && <Button type="button" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>}
              <Button disabled={!editing.keywords.length}>Save</Button>
            </div>
          </form>
        </Card>
      )}
      {searches.length === 0 && !editing && <Empty>No searches yet. Add your first niche.</Empty>}
      <div className="grid gap-3 md:grid-cols-2">
        {searches.map((s) => (
          <Card key={s.id} title={<span className="flex items-center gap-2">{s.label} {!s.active && <Badge>paused</Badge>}</span>}
            action={<Button size="sm" variant="ghost" onClick={() => setEditing({ ...s, min_salary: s.min_salary ?? "" })}>Edit</Button>}>
            <p className="text-sm text-slate-500">{s.keywords.join(" · ")}</p>
            <p className="text-sm text-slate-500">{s.location} + {s.radius_miles} miles{s.min_salary ? ` · from £${s.min_salary}` : ""}</p>
            <dl className="mt-3 grid grid-cols-3 gap-2 text-center text-sm">
              <div><dt className="text-xs text-slate-500">Ads last run</dt><dd className="font-semibold">{s.last_run?.listings_seen ?? "–"}</dd></div>
              <div><dt className="text-xs text-slate-500">Hot leads</dt><dd className="font-semibold"><Link href={`/hot-leads?search_id=${s.id}`}>{s.hot_leads}</Link></dd></div>
              <div><dt className="text-xs text-slate-500">Agencies</dt><dd className="font-semibold"><Link href={`/agencies?search_id=${s.id}`}>{s.agencies}</Link></dd></div>
            </dl>
            {s.sources && Object.keys(s.sources).length > 0 && (
              <p className="mt-2 flex flex-wrap gap-1 text-xs" aria-label="Ads per source, last 14 days">
                {Object.entries(s.sources).sort((a: any, b: any) => b[1] - a[1]).map(([k, v]) => (
                  <Badge key={k}>{k}: {String(v)}</Badge>))}
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
              <span>Last run {ago(s.last_run_at)}{s.last_run?.status === "error" && <Badge tone="red" className="ml-1" title={s.last_run.error}>error</Badge>}</span>
              {s.owner ? <Badge tone="green">Owned by {s.owner.agency}</Badge> : <Badge tone="blue">Territory available</Badge>}
            </div>
            <div className="mt-3 flex items-center gap-2">
              <Button size="sm" onClick={() => runNow(s.id)} disabled={!!running[s.id]?.endsWith("…")} data-testid={`run-${s.id}`}>Run now</Button>
              {running[s.id] && <span className="text-sm text-slate-600 dark:text-slate-300" role="status">{running[s.id]}</span>}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
