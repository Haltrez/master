"use client";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { ACTIVITY_LABEL } from "@/lib/format";
import { Modal } from "./modal";
import { Button, ErrorNote, Input, Label, Select } from "./ui";

// Changing stage: "contacted" asks how (and logs it); "pilot" opens the client form.
export function useStageChange(onDone: (agencyId: number, stage: string) => void) {
  const [pending, setPending] = useState<{ agency: any; stage: string; kind: "how" | "pilot" } | null>(null);
  async function change(agency: any, stage: string) {
    if (stage === agency.stage) return;
    if (stage === "pilot") { setPending({ agency, stage, kind: "pilot" }); return; }
    try {
      await api(`/agencies/${agency.id}`, { method: "PATCH", body: { stage } });
      onDone(agency.id, stage);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setPending({ agency, stage, kind: "how" });
      else alert((e as Error).message);
    }
  }
  const dialog = pending && (pending.kind === "how"
    ? <HowDialog agency={pending.agency} onClose={() => setPending(null)} onDone={() => { onDone(pending.agency.id, "contacted"); setPending(null); }} />
    : <PilotDialog agency={pending.agency} onClose={() => setPending(null)} onDone={() => { onDone(pending.agency.id, "pilot"); setPending(null); }} />);
  return { change, dialog };
}

function HowDialog({ agency, onClose, onDone }: { agency: any; onClose: () => void; onDone: () => void }) {
  async function pick(type: string) {
    await api(`/agencies/${agency.id}`, { method: "PATCH", body: { stage: "contacted", log_activity_type: type } });
    onDone();
  }
  return (
    <Modal title="How did you contact them?" onClose={onClose}>
      <p className="mb-3 text-sm text-slate-500">{agency.name} moves to Contacted and the action counts toward today.</p>
      <div className="grid grid-cols-2 gap-2">
        {["email", "call", "linkedin_dm", "linkedin_connect"].map((t) => <Button key={t} variant="secondary" onClick={() => pick(t)}>{ACTIVITY_LABEL[t]}</Button>)}
      </div>
    </Modal>
  );
}

export function PilotDialog({ agency, onClose, onDone, plan: initialPlan = "pilot" }: { agency: any; onClose: () => void; onDone: () => void; plan?: string }) {
  const [searches, setSearches] = useState<any[]>([]);
  const [picked, setPicked] = useState<number[]>([]);
  const [form, setForm] = useState({ plan: initialPlan, monthly_price_gbp: initialPlan === "pilot" ? 0 : 1250, sheet_url: "", delivery_email: "" });
  const [err, setErr] = useState<string>();
  useEffect(() => { api<any[]>("/searches").then(setSearches).catch(() => {}); }, []);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/clients", { body: { agency_id: agency.id, ...form, monthly_price_gbp: Number(form.monthly_price_gbp),
        sheet_url: form.sheet_url || null, delivery_email: form.delivery_email || null, search_ids: picked } });
      onDone();
    } catch (e: any) { setErr(e.message); }
  }
  return (
    <Modal title={`Start ${form.plan === "pilot" ? "a pilot" : "a client"}: ${agency.name}`} onClose={onClose}>
      <form onSubmit={save} className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div><Label>Plan</Label><Select value={form.plan} onChange={(e) => setForm({ ...form, plan: e.target.value })}>
            <option value="pilot">Free 30-day pilot</option><option value="founding">Founding</option><option value="done_for_you">Done for you</option></Select></div>
          <div><Label>£ per month</Label><Input type="number" min={0} value={form.monthly_price_gbp} onChange={(e) => setForm({ ...form, monthly_price_gbp: e.target.value as any })} /></div>
        </div>
        <div>
          <Label>Territories (exclusive)</Label>
          <div className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-slate-200 p-2 dark:border-slate-700">
            {searches.length === 0 && <p className="text-sm text-slate-500">No searches yet.</p>}
            {searches.map((s) => {
              const owned = s.owner && s.owner.agency !== agency.name;
              return (
                <label key={s.id} className={`flex items-center gap-2 text-sm ${owned ? "text-slate-400" : ""}`}>
                  <input type="checkbox" disabled={!!owned} checked={picked.includes(s.id)}
                    onChange={(e) => setPicked((p) => e.target.checked ? [...p, s.id] : p.filter((x) => x !== s.id))} />
                  {s.label} {owned && <span className="text-xs">(owned by {s.owner.agency})</span>}
                </label>
              );
            })}
          </div>
        </div>
        <div><Label>Google Sheet link (optional, Make.com can create it)</Label><Input value={form.sheet_url} onChange={(e) => setForm({ ...form, sheet_url: e.target.value })} placeholder="https://docs.google.com/spreadsheets/…" /></div>
        <div><Label>Delivery email</Label><Input type="email" value={form.delivery_email} onChange={(e) => setForm({ ...form, delivery_email: e.target.value })} /></div>
        <ErrorNote error={err} />
        <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button>Create client</Button></div>
      </form>
    </Modal>
  );
}
