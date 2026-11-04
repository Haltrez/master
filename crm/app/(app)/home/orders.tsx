"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Input, Label, Progress, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";

const TONE: Record<string, "blue" | "green" | "amber" | "red" | "slate"> = {
  collecting: "blue", waiting: "amber", done: "green", paused: "slate", error: "red" };
const LABEL: Record<string, string> = {
  collecting: "searching…", waiting: "topping up", done: "complete", paused: "paused", error: "problem" };

export function Orders({ initial, industries, google }: { initial: any[]; industries: any[]; google?: any }) {
  const [orders, setOrders] = useState<any[]>(initial);
  const [showForm, setShowForm] = useState(initial.length === 0);
  const refresh = useCallback(() => api<any[]>("/orders").then(setOrders).catch(() => {}), []);

  useEffect(() => {
    const on = () => refresh();
    window.addEventListener("orders-changed", on);
    return () => window.removeEventListener("orders-changed", on);
  }, [refresh]);
  const busy = orders.some((o) => o.status === "collecting");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [busy, refresh]);

  return (
    <>
      {google && !google.connected && (
        <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          Google isn't connected, so leads go to a CSV download instead of a shared sheet.{" "}
          {google.client_set ? <a href="/api/google/start" className="font-medium">Connect Google</a>
            : <Link href="/settings">How to connect Google</Link>}
        </p>
      )}
      <Card title="New lead order" action={!showForm && <Button size="sm" onClick={() => setShowForm(true)}>+ New order</Button>}>
        {showForm ? <OrderForm industries={industries} onDone={() => { setShowForm(false); refresh(); }}
          onCancel={orders.length ? () => setShowForm(false) : undefined} />
          : <p className="text-sm text-slate-500">Agency, industry, region, how many leads: it finds them and sends them out.</p>}
      </Card>
      <Card title={`Orders (${orders.length})`}>
        {orders.length === 0 ? <Empty>No orders yet.</Empty> : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {orders.map((o) => <OrderRow key={o.id} o={o} onChange={refresh} />)}
          </ul>
        )}
      </Card>
    </>
  );
}

function OrderRow({ o, onChange }: { o: any; onChange: () => void }) {
  const [err, setErr] = useState<string>();
  const [working, setWorking] = useState(false);
  const sent = o.frequency === "weekly" ? o.delivered_period : o.delivered_total;
  async function act(fn: () => Promise<any>) {
    setErr(undefined); setWorking(true);
    try { await fn(); onChange(); } catch (e: any) { setErr(e.message); } finally { setWorking(false); }
  }
  return (
    <li className="py-3" data-testid="order">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium">{o.agency_name} <span className="font-normal text-slate-500">· {o.industry} · {o.location}</span></p>
          <p className="text-xs text-slate-500">
            {o.frequency === "weekly" ? `${o.leads_wanted} a week` : `${o.leads_wanted} leads`}
            {" · "}{o.package === "outreach" ? "sheet + outreach" : "sheet"}
            {o.client_email && <> · {o.client_email}</>}
            {o.last_run_at && <> · updated {ago(o.last_run_at)}</>}
          </p>
        </div>
        <Badge tone={TONE[o.status]}>{LABEL[o.status] || o.status}</Badge>
      </div>
      <div className="mt-2 flex items-center gap-3">
        <div className="flex-1"><Progress value={sent} max={o.leads_wanted} /></div>
        <span className="text-sm tabular-nums" data-testid="order-progress">{sent} / {o.leads_wanted}</span>
      </div>
      {o.note && <p className="mt-1 text-xs text-slate-500">{o.note}{o.ready > 0 && o.status !== "done" ? ` ${o.ready} ready to send.` : ""}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
        {o.sheet_url && <a href={o.sheet_url} target="_blank" rel="noreferrer" className="font-medium">Open sheet ↗</a>}
        <a href={`/api/engine/orders/${o.id}/leads.csv`}>CSV</a>
        {o.status !== "paused" && o.status !== "done" && (
          <Button size="sm" variant="secondary" disabled={working || o.status === "collecting"}
            onClick={() => act(() => api(`/orders/${o.id}/send-more`, { method: "POST" }))}>Search &amp; send more now</Button>
        )}
        {o.status === "done" && (
          <Button size="sm" variant="secondary" disabled={working}
            onClick={() => {
              const n = Number(prompt("Total number of leads for this order?", String(o.leads_wanted + 10)));
              if (n > o.leads_wanted) act(async () => {
                await api(`/orders/${o.id}`, { method: "PATCH", body: { leads_wanted: n, status: "waiting" } });
                await api(`/orders/${o.id}/send-more`, { method: "POST" });
              });
            }}>Add more leads</Button>
        )}
        {o.status === "paused"
          ? <Button size="sm" variant="ghost" disabled={working} onClick={() => act(() => api(`/orders/${o.id}`, { method: "PATCH", body: { status: "waiting" } }))}>Resume</Button>
          : o.status !== "done" && <Button size="sm" variant="ghost" disabled={working} onClick={() => act(() => api(`/orders/${o.id}`, { method: "PATCH", body: { status: "paused" } }))}>Pause</Button>}
        <Button size="sm" variant="ghost" className="ml-auto text-red-600" disabled={working}
          onClick={() => {
            if (confirm(`Delete the ${o.agency_name} order (${o.industry}, ${o.location})? ${o.industry} in ${o.location} becomes free for another agency. A Google Sheet already made stays in your Drive.`))
              act(() => api(`/orders/${o.id}`, { method: "DELETE" }));
          }}>Delete</Button>
      </div>
      <ErrorNote error={err} />
    </li>
  );
}

function OrderForm({ industries, onDone, onCancel }: { industries: any[]; onDone: () => void; onCancel?: () => void }) {
  const [industry, setIndustry] = useState(industries[0]?.name || "");
  const [custom, setCustom] = useState("");
  const [err, setErr] = useState<string>();
  const [busy, setBusy] = useState(false);
  const preset = industries.find((i) => i.name === industry);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const keywords = String(f.get("keywords") || "").split(",").map((s) => s.trim()).filter(Boolean);
    setErr(undefined); setBusy(true);
    try {
      await api("/orders", { body: {
        agency_name: f.get("agency_name"), client_email: String(f.get("client_email") || "") || null,
        industry: industry === "__other" ? custom : industry, location: f.get("location"),
        radius_miles: Number(f.get("radius_miles")), leads_wanted: Number(f.get("leads_wanted")),
        frequency: f.get("frequency"), package: f.get("package"), keywords: keywords.length ? keywords : null } });
      onDone();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
      <div><Label htmlFor="agency_name">Agency</Label><Input id="agency_name" name="agency_name" required placeholder="Apex Recruitment Ltd" /></div>
      <div><Label htmlFor="client_email">Share the sheet with (email)</Label><Input id="client_email" name="client_email" type="email" placeholder="jo@apex.co.uk (optional)" /></div>
      <div>
        <Label htmlFor="industry">Industry</Label>
        <Select id="industry" value={industry} onChange={(e) => setIndustry(e.target.value)}>
          {industries.map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}
          <option value="__other">Other…</option>
        </Select>
        {industry === "__other"
          ? <Input className="mt-2" required value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="e.g. Dental" aria-label="Industry name" />
          : preset && <p className="mt-1 text-xs text-slate-500">Searches: {preset.keywords.join(", ")}</p>}
      </div>
      <div><Label htmlFor="location">Region (town or city)</Label><Input id="location" name="location" required placeholder="Manchester" /></div>
      <div className="grid grid-cols-2 gap-3">
        <div><Label htmlFor="leads_wanted">Number of leads</Label><Input id="leads_wanted" name="leads_wanted" type="number" min={1} max={500} defaultValue={20} required /></div>
        <div><Label htmlFor="radius_miles">Radius</Label>
          <Select id="radius_miles" name="radius_miles" defaultValue="20">
            {[10, 20, 30, 50].map((r) => <option key={r} value={r}>{r} miles</option>)}
          </Select></div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div><Label htmlFor="frequency">How often</Label>
          <Select id="frequency" name="frequency" defaultValue="once"><option value="once">Once</option><option value="weekly">Every week</option></Select></div>
        <div><Label htmlFor="package">Package</Label>
          <Select id="package" name="package" defaultValue="sheet"><option value="sheet">Sheet only</option><option value="outreach">Sheet + I do outreach</option></Select></div>
      </div>
      <details className="sm:col-span-2 text-sm">
        <summary className="cursor-pointer text-slate-500">Custom job titles (optional)</summary>
        <Input className="mt-2" name="keywords" placeholder="dental nurse, practice manager  (comma separated: replaces the industry's list)" />
      </details>
      <div className="flex items-center gap-2 sm:col-span-2">
        <Button disabled={busy}>{busy ? "Creating…" : "Create order & find leads"}</Button>
        {onCancel && <Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button>}
      </div>
      <div className="sm:col-span-2"><ErrorNote error={err} /></div>
    </form>
  );
}
