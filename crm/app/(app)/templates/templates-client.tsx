"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, Card, ErrorNote, Input, Label, Select, Textarea, cn } from "@/components/ui";
import { api } from "@/lib/api";

const VARS = ["first_name", "agency", "niche", "city", "lead1_employer", "lead1_role", "lead1_count", "lead1_since",
  "lead2_employer", "lead2_role", "lead2_count", "lead3_employer", "lead3_role", "lead3_count", "hm_role", "open_since", "sender_name",
  "company", "posted_since"];
const FOOTER = "{sender_name}, SmartEmploy\nSmartEmploy is a trading name of SIA Impresso, Latvia\nNot interested? Reply \"stop\" and I won't email again.";

export function TemplatesClient({ templates, agencies }: { templates: any[]; agencies: any[] }) {
  const router = useRouter();
  const [sel, setSel] = useState<any>(templates[0] || { name: "", channel: "email", subject: "", body: "" });
  const [agencyId, setAgencyId] = useState<number | undefined>(agencies.find((a) => a.email_allowed)?.id || agencies[0]?.id);
  const [prev, setPrev] = useState<any>(null);
  const [err, setErr] = useState<string>();
  useEffect(() => {
    const t = setTimeout(() => {
      api("/templates/preview", { body: { subject: sel.subject || null, body: sel.body || "", agency_id: agencyId } }).then(setPrev).catch(() => setPrev(null));
    }, 300);
    return () => clearTimeout(t);
  }, [sel.subject, sel.body, agencyId]);
  async function save() {
    setErr(undefined);
    const body = { name: sel.name, channel: sel.channel, subject: sel.subject || null, body: sel.body };
    if (sel.channel === "email" && !sel.body.includes('Reply "stop"')) { setErr("Every email needs the sender footer with the opt-out line."); return; }
    try {
      const saved = sel.id ? await api(`/templates/${sel.id}`, { method: "PATCH", body }) : await api("/templates", { body });
      setSel(saved); router.refresh();
    } catch (e: any) { setErr(e.message); }
  }
  async function remove() { if (sel.id && confirm(`Delete ${sel.name}?`)) { await api(`/templates/${sel.id}`, { method: "DELETE" }); setSel(templates[0]); router.refresh(); } }
  return (
    <div className="grid gap-4 lg:grid-cols-[14rem_1fr_1fr]">
      <Card>
        <ul className="space-y-1 text-sm">
          {templates.map((t) => <li key={t.id}><button onClick={() => setSel(t)} className={cn("w-full rounded-md px-2 py-1 text-left", sel.id === t.id && "bg-brand-soft text-brand dark:bg-blue-950")}>{t.name}<span className="block text-xs text-slate-500">{t.channel}</span></button></li>)}
        </ul>
        <Button size="sm" variant="secondary" className="mt-3 w-full" onClick={() => setSel({ name: "New template", channel: "email", subject: "", body: `Hi {first_name},\n\n\n\n${FOOTER}` })}>New template</Button>
      </Card>
      <Card title="Edit">
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2"><div><Label>Name</Label><Input value={sel.name} onChange={(e) => setSel({ ...sel, name: e.target.value })} /></div>
            <div><Label>Channel</Label><Select value={sel.channel} onChange={(e) => setSel({ ...sel, channel: e.target.value })}>
              <option value="email">Email</option><option value="linkedin">LinkedIn</option><option value="client_hm_email">Client opener (sheet)</option></Select></div></div>
          {sel.channel === "email" && <div><Label>Subject</Label><Input value={sel.subject || ""} onChange={(e) => setSel({ ...sel, subject: e.target.value })} /></div>}
          <div><Label>Body</Label><Textarea rows={14} value={sel.body} onChange={(e) => setSel({ ...sel, body: e.target.value })} className="font-mono text-xs" /></div>
          <ErrorNote error={err} />
          <div className="flex gap-2"><Button onClick={save}>Save</Button>{sel.id && <Button variant="ghost" onClick={remove}>Delete</Button>}</div>
          <div><Label>Variables (click to insert)</Label><div className="flex flex-wrap gap-1">
            {VARS.map((v) => <button key={v} onClick={() => setSel({ ...sel, body: `${sel.body}{${v}}` })} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs dark:bg-slate-800">{`{${v}}`}</button>)}</div></div>
        </div>
      </Card>
      <Card title="Live preview">
        <Label>Against agency</Label>
        <Select value={agencyId} onChange={(e) => setAgencyId(Number(e.target.value))} className="mb-3">{agencies.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select>
        {prev?.blocked && <ErrorNote error={prev.blocked} />}
        {prev?.missing?.length > 0 && <p className="mb-2 rounded-lg bg-red-50 p-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">Empty for this agency: {prev.missing.join(", ")}</p>}
        {prev?.subject && <p className="mb-1 text-sm"><span className="text-slate-500">Subject:</span> {prev.subject}</p>}
        <pre className="whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-sans text-sm dark:bg-slate-950">{prev?.body}</pre>
      </Card>
    </div>
  );
}
