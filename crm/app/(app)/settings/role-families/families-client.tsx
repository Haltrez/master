"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { TagInput } from "@/components/tag-input";
import { Badge, Button, Card, Input, Label, cn } from "@/components/ui";
import { api } from "@/lib/api";

export function FamiliesClient({ families }: { families: any[] }) {
  const router = useRouter();
  const [sel, setSel] = useState<any>(families[0] || { name: "", exact_titles: [], related_titles: [] });
  const [preview, setPreview] = useState<any[]>([]);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (!sel.id) { setPreview([]); return; }
    const qs = new URLSearchParams();
    sel.exact_titles.forEach((t: string) => qs.append("exact_titles", t));
    sel.related_titles.forEach((t: string) => qs.append("related_titles", t));
    const t = setTimeout(() => api(`/role-families/${sel.id}/preview?${qs}`).then(setPreview).catch(() => setPreview([])), 300);
    return () => clearTimeout(t);
  }, [sel]);
  async function save() {
    const body = { name: sel.name, exact_titles: sel.exact_titles, related_titles: sel.related_titles };
    const r = sel.id ? await api(`/role-families/${sel.id}`, { method: "PATCH", body }) : await api("/role-families", { body });
    setSel(r); setSaved(true); setTimeout(() => setSaved(false), 1200); router.refresh();
  }
  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_1fr_1fr]">
      <Card>
        <ul className="space-y-1 text-sm">{families.map((f) => (
          <li key={f.id}><button onClick={() => setSel(f)} className={cn("w-full rounded-md px-2 py-1 text-left", sel.id === f.id && "bg-brand-soft text-brand dark:bg-blue-950")}>{f.name}</button></li>))}
        </ul>
        <Button size="sm" variant="secondary" className="mt-3 w-full" onClick={() => setSel({ name: "", exact_titles: [], related_titles: [] })}>New family</Button>
      </Card>
      <Card title="Titles">
        <div className="space-y-3">
          <div><Label>Name</Label><Input value={sel.name} onChange={(e) => setSel({ ...sel, name: e.target.value })} placeholder="Electrical Estimator" /></div>
          <div><Label>Exact titles (+4)</Label><TagInput value={sel.exact_titles} onChange={(v) => setSel({ ...sel, exact_titles: v })} /></div>
          <div><Label>Related titles (+2)</Label><TagInput value={sel.related_titles} onChange={(v) => setSel({ ...sel, related_titles: v })} /></div>
          <Button onClick={save} disabled={!sel.name || !sel.exact_titles.length}>{saved ? "Saved ✓" : "Save"}</Button>
        </div>
      </Card>
      <Card title={`Stored jobs this family matches (${preview.length})`}>
        {!sel.id ? <p className="text-sm text-slate-500">Save the family to preview.</p> : preview.length === 0 ? <p className="text-sm text-slate-500">No stored jobs match yet.</p> : (
          <ul className="space-y-1 text-sm">{preview.slice(0, 100).map((p) => (
            <li key={p.job_id}>{p.title} <span className="text-slate-500">· {p.company}</span> <Badge tone={p.kind === "exact" ? "green" : "slate"}>{p.kind}</Badge>{p.seniority && <Badge tone="amber" className="ml-1">{p.seniority}</Badge>}</li>))}</ul>
        )}
      </Card>
    </div>
  );
}
