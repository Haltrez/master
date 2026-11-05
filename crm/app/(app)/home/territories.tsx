"use client";
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card, ErrorNote, Input, Select } from "@/components/ui";
import { api, waitFor } from "@/lib/api";
import { ago } from "@/lib/format";

// Industry + town collected every morning before it's sold, so reposts build up and you can see how
// many verified leads are ready before pitching an agency.
export function Territories({ industries }: { industries: any[] }) {
  const [list, setList] = useState<any[]>([]);
  const [err, setErr] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Record<number, any>>({});
  const refresh = useCallback(() => api<any[]>("/territories").then(setList).catch(() => {}), []);
  useEffect(() => { refresh(); }, [refresh]);

  async function add(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setErr(undefined); setBusy(true);
    try {
      await api("/territories", { body: { industry: f.get("industry"), location: f.get("location") } });
      (e.target as HTMLFormElement).reset();
      refresh();
    } catch (x: any) { setErr(x.message); } finally { setBusy(false); }
  }

  async function check(id: number) {
    setPreview((p) => ({ ...p, [id]: { running: true } }));
    try {
      const { task_id } = await api<any>(`/territories/${id}/preview`, { method: "POST" });
      const t = await waitFor<any>(`/tasks/${task_id}`, (x) => x.status !== "running", 2000, 5 * 60_000);
      setPreview((p) => ({ ...p, [id]: t.status === "ok" ? t.result : { error: t.error || "check failed" } }));
    } catch (x: any) { setPreview((p) => ({ ...p, [id]: { error: x.message } })); }
  }

  return (
    <Card title="Territories (collected every morning)">
      <p className="mb-3 text-sm text-slate-500">Start collecting an industry + town before you sell it. Reposts build up day by day,
        so the order starts with history. "Check ready leads" runs every check without sending anything.</p>
      <form onSubmit={add} className="mb-3 flex flex-wrap gap-2">
        <Select name="industry" aria-label="Territory industry" className="w-auto">
          {industries.map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}
        </Select>
        <Input name="location" required placeholder="Town or city" aria-label="Territory town" className="w-40" />
        <Button disabled={busy} variant="secondary">Start collecting</Button>
      </form>
      <ErrorNote error={err} />
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {list.map((t) => {
          const p = preview[t.search_id];
          return (
            <li key={t.search_id} className="py-2 text-sm" data-testid="territory">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <span className="font-medium">{t.label}</span>{" "}
                  {t.owner ? <Badge tone="blue">{t.owner}</Badge> : <Badge>not sold</Badge>}
                  {!t.active && <> <Badge tone="amber">stopped</Badge></>}
                  <p className="text-xs text-slate-500">{t.ads} {t.ads === 1 ? "ad" : "ads"} · {t.reposted_roles} reposted roles
                    {t.collecting_since && <> · since {t.collecting_since}</>}{t.last_run_at && <> · ran {ago(t.last_run_at)}</>}</p>
                </div>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" disabled={p?.running} onClick={() => check(t.search_id)}>
                    {p?.running ? "Checking…" : "Check ready leads"}</Button>
                  {!t.owner && (
                    <Button size="sm" variant="ghost" className="text-slate-500"
                      onClick={async () => { await api(`/searches/${t.search_id}`, { method: "PATCH", body: { active: !t.active } }); refresh(); }}>
                      {t.active ? "Stop" : "Restart"}</Button>
                  )}
                </div>
              </div>
              {p && !p.running && (p.error ? <ErrorNote error={p.error} /> : (
                <div className="mt-1 rounded-lg bg-slate-50 p-2 text-xs dark:bg-slate-900">
                  <b>{p.ready} verified leads ready now.</b>{" "}
                  {p.sample?.slice(0, 5).map((s: any) => `${s.Company} (${s.Role})`).join(" · ")}
                </div>
              ))}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
