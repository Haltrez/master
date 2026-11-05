"use client";
import { useState } from "react";
import { Button, Card, ErrorNote, Input, Label } from "@/components/ui";
import { api } from "@/lib/api";


export function SettingsForm({ settings }: { settings: any }) {
  const [f, setF] = useState({ repost_threshold: settings.repost_threshold ?? 3, daily_outreach_target: settings.daily_outreach_target ?? 100,
    sender_name: settings.sender_name ?? "", make_webhooks: settings.make_webhooks ?? {},
    reposts_only: settings.reposts_only ?? true, verified_companies_only: settings.verified_companies_only ?? true });
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string>();
  async function save(e: React.FormEvent) {
    e.preventDefault(); setErr(undefined);
    try { await api("/settings", { method: "PATCH", body: { ...f, repost_threshold: Number(f.repost_threshold), daily_outreach_target: Number(f.daily_outreach_target) } });
      setSaved(true); setTimeout(() => setSaved(false), 1500); } catch (e: any) { setErr(e.message); }
  }
  return (
    <Card title="Preferences">
      <form onSubmit={save} className="space-y-3">
        <div className="max-w-xs"><Label>Times a role must be posted</Label><Input type="number" min={2} value={f.repost_threshold} onChange={(e) => setF({ ...f, repost_threshold: e.target.value })} /></div>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={!!f.reposts_only} onChange={(e) => setF({ ...f, reposts_only: e.target.checked })} />
          <span><b>Reposts only</b>: a lead must have been advertised that many separate times (copies on the same day
            count once), or marked as a repost by the board, or found in the web archive. Leave this on.</span>
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={!!f.verified_companies_only} onChange={(e) => setF({ ...f, verified_companies_only: e.target.checked })} />
          <span><b>Real companies only</b>: sheets and CSVs only include employers confirmed as active companies on
            Companies House, and never recruitment businesses (SIC 78xxx). Needs the Companies House key.</span>
        </label>
        <ErrorNote error={err} />
        <Button>{saved ? "Saved ✓" : "Save"}</Button>
      </form>
    </Card>
  );
}
