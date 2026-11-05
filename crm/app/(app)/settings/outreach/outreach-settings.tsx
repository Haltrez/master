"use client";
import { useState } from "react";
import { Badge, Button, Card, ErrorNote, Input, Label, Select } from "@/components/ui";
import { api } from "@/lib/api";

export function OutreachSettings({ initial, campaigns, campaignsError }: { initial: any; campaigns: any[]; campaignsError?: string }) {
  const [f, setF] = useState({ main_campaign_id: initial.main_campaign_id ?? "", fallback_campaign_id: initial.fallback_campaign_id ?? "",
    daily_push_limit: initial.daily_push_limit ?? 50, auto_push: !!initial.auto_push, send_window: initial.send_window ?? "" });
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string>();
  const [conn, setConn] = useState<any>(null);
  async function save(e: React.FormEvent) {
    e.preventDefault(); setErr(undefined);
    try {
      await api("/outreach/settings", { method: "PATCH", body: { ...f, daily_push_limit: Number(f.daily_push_limit),
        main_campaign_id: f.main_campaign_id ? Number(f.main_campaign_id) : null, fallback_campaign_id: f.fallback_campaign_id ? Number(f.fallback_campaign_id) : null } });
      setSaved(true); setTimeout(() => setSaved(false), 1500);
    } catch (x: any) { setErr(x.message); }
  }
  const pick = (k: "main_campaign_id" | "fallback_campaign_id", label: string) => (
    <div><Label htmlFor={k}>{label}</Label>
      <Select id={k} value={String(f[k] ?? "")} onChange={(e) => setF({ ...f, [k]: e.target.value })}>
        <option value="">(not set)</option>
        {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.status} · #{c.id}</option>)}
      </Select></div>
  );
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Campaigns and limits">
        <form onSubmit={save} className="space-y-3">
          {campaignsError && <ErrorNote error={`Couldn't load Smartlead campaigns: ${campaignsError}`} />}
          {pick("main_campaign_id", "Main campaign (2 hot leads per email)")}
          {pick("fallback_campaign_id", "Fallback campaign (agencies with only 1 hot lead)")}
          <div className="max-w-xs"><Label htmlFor="limit">Daily push limit</Label>
            <Input id="limit" type="number" min={1} max={400} value={f.daily_push_limit} onChange={(e) => setF({ ...f, daily_push_limit: Number(e.target.value) })} /></div>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={f.auto_push} onChange={(e) => setF({ ...f, auto_push: e.target.checked })} />
            <span><b>Auto-push</b>: every day at 07:30 UK the queue is built and pushed without manual approval. Blocked rows are still never sent.</span></label>
          <div><Label htmlFor="window">Send window note</Label>
            <Input id="window" value={f.send_window} placeholder="e.g. Mon-Thu 8:00-11:00 UK (set in each Smartlead campaign's schedule)"
              onChange={(e) => setF({ ...f, send_window: e.target.value })} /></div>
          <ErrorNote error={err} />
          <Button>{saved ? "Saved ✓" : "Save"}</Button>
        </form>
      </Card>
      <Card title="Smartlead connection">
        <p className="text-sm">API key: {initial.key_set ? <Badge tone="green">set on the server</Badge> : <Badge tone="red">missing</Badge>}</p>
        <p className="mt-1 text-sm">Test emails go to: {initial.owner_test_email || <span className="text-slate-500">OWNER_TEST_EMAIL not set</span>}</p>
        <div className="mt-3 flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={async () => setConn(await api("/outreach/test-connection", { method: "POST" }).catch((e) => ({ ok: false, error: e.message })))}>
            Test connection</Button>
          {conn && (conn.ok ? <Badge tone="green">OK</Badge> : <span className="text-sm text-red-600">{conn.error}</span>)}
        </div>
        <p className="mt-3 text-xs text-slate-500">The key lives only in the server's .env. Every Smartlead call goes through the engine.</p>
      </Card>
    </div>
  );
}
