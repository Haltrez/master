"use client";
import { useState } from "react";
import { Button, Card, ErrorNote, Input, Label } from "@/components/ui";
import { api } from "@/lib/api";

const HOOKS = [["new_client", "New client onboarding (Scenario 4)"], ["matches_to_sheet", "Candidate matches to Sheet (Scenario 5)"]];

export function SettingsForm({ settings }: { settings: any }) {
  const [f, setF] = useState({ repost_threshold: settings.repost_threshold ?? 3, daily_outreach_target: settings.daily_outreach_target ?? 100,
    sender_name: settings.sender_name ?? "", make_webhooks: settings.make_webhooks ?? {} });
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
        <div className="grid grid-cols-2 gap-3">
          <div><Label>Daily outreach target</Label><Input type="number" min={1} value={f.daily_outreach_target} onChange={(e) => setF({ ...f, daily_outreach_target: e.target.value })} /></div>
          <div><Label>Repost threshold</Label><Input type="number" min={2} value={f.repost_threshold} onChange={(e) => setF({ ...f, repost_threshold: e.target.value })} /></div>
        </div>
        <div><Label>Sender name (email footer)</Label><Input value={f.sender_name} onChange={(e) => setF({ ...f, sender_name: e.target.value })} /></div>
        {HOOKS.map(([k, label]) => (
          <div key={k}><Label>Make.com webhook: {label}</Label>
            <Input value={f.make_webhooks[k] || ""} placeholder="https://hook.eu1.make.com/…" onChange={(e) => setF({ ...f, make_webhooks: { ...f.make_webhooks, [k]: e.target.value } })} /></div>
        ))}
        <ErrorNote error={err} />
        <Button>{saved ? "Saved ✓" : "Save"}</Button>
      </form>
    </Card>
  );
}
