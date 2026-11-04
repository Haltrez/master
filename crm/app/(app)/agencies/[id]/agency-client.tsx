"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Candidates } from "@/components/candidates";
import { PilotDialog, useStageChange } from "@/components/stage-change";
import { Badge, Button, Card, ComplianceBadge, ErrorNote, Input, Label, Select, Textarea, cn } from "@/components/ui";
import { api, waitFor } from "@/lib/api";
import { ACTIVITY_LABEL, d, dt, STAGES, STAGE_LABEL } from "@/lib/format";
import { QuickLog } from "../../today/quick-log";

export function AgencyClient({ bundle, templates, families }: { bundle: any; templates: any[]; families: any[] }) {
  const router = useRouter();
  const a = bundle.agency;
  const [tab, setTab] = useState<"overview" | "candidates">("overview");
  useEffect(() => { if (window.location.hash === "#candidates") setTab("candidates"); }, []);
  const refresh = () => router.refresh();
  const { change, dialog } = useStageChange(() => refresh());
  const [enriching, setEnriching] = useState<string>();
  const [convert, setConvert] = useState(false);

  async function patch(body: any) { await api(`/agencies/${a.id}`, { method: "PATCH", body }); refresh(); }
  async function enrich() {
    setEnriching("Checking Companies House…");
    try {
      const { task_id } = await api(`/agencies/${a.id}/enrich`, { method: "POST" });
      const t = await waitFor<any>(`/tasks/${task_id}`, (x) => x.status !== "running");
      setEnriching(t.status === "ok" ? t.result.companies_house : `error: ${t.error}`);
      refresh();
    } catch (e: any) { setEnriching(`error: ${e.message}`); }
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{a.name}</h1>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-500">
            <ComplianceBadge agency={a} /> {!a.email_allowed && <span className="text-amber-700 dark:text-amber-400">{a.compliance}</span>}
            {a.ch_company_number && <a href={`https://find-and-update.company-information.service.gov.uk/company/${a.ch_company_number}`} target="_blank" rel="noreferrer">CH {a.ch_company_number}</a>}
            {a.ch_company_status && <span>· {a.ch_company_status}</span>} · {a.ads_count_90d} {a.ads_count_90d === 1 ? "ad" : "ads"} in 90 days
            {a.ch_match_confidence != null && <span>· match {Math.round(a.ch_match_confidence * 100)}%</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={a.stage} onChange={(e) => change(a, e.target.value)} aria-label="Stage" className="w-40" data-testid="stage-select">
            {STAGES.map((s) => <option key={s} value={s}>{STAGE_LABEL[s]}</option>)}</Select>
          {a.stage === "pilot" && <Button variant="secondary" onClick={() => setConvert(true)}>Convert pilot to paid</Button>}
          <Button variant="secondary" onClick={enrich} disabled={enriching?.endsWith("…")}>Companies House</Button>
        </div>
      </div>
      {enriching && <p className="mb-3 text-sm text-slate-600 dark:text-slate-300" role="status">{enriching}</p>}

      <div className="mb-4 flex gap-1 border-b border-slate-200 dark:border-slate-800">
        {(["overview", "candidates"] as const).map((t) => (
          <button key={t} onClick={() => { setTab(t); history.replaceState(null, "", t === "candidates" ? "#candidates" : "#"); }}
            className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === t ? "border-brand font-medium text-brand" : "border-transparent text-slate-500")}>
            {t === "overview" ? "Overview" : `Candidates (${bundle.briefs.length})`}
          </button>
        ))}
      </div>

      {tab === "candidates" ? <Candidates agencyId={a.id} briefs={bundle.briefs} matches={bundle.matches} families={families} /> : (
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-2">
            <DraftPanel agency={a} templates={templates} contacts={bundle.contacts} onLogged={refresh} />
            <Card title="Top hot leads in their niche">
              {bundle.top_hot_leads.length === 0 ? <p className="text-sm text-slate-500">No hot leads in {bundle.niche_search?.label || "their niche"} yet.</p> : (
                <ul className="space-y-1 text-sm">{bundle.top_hot_leads.map((h: any) => (
                  <li key={h.lead_key} className="flex justify-between gap-2"><span><b>{h.employer_name}</b>: {h.title} · {h.posting_count_90d}× since {d(h.first_seen_at)}</span>
                    <Badge tone="blue">{h.score}</Badge></li>))}</ul>
              )}
            </Card>
            <Card title="Activity">
              <QuickLog agencyId={a.id} types={["email", "call", "linkedin_dm", "linkedin_connect", "meeting", "note"]} onLogged={refresh} />
              <ul className="mt-3 space-y-2 text-sm">
                {bundle.activities.length === 0 && <li className="text-slate-500">Nothing logged yet.</li>}
                {bundle.activities.map((t: any) => (
                  <li key={t.id} className="flex gap-3"><span className="w-28 shrink-0 text-slate-500">{dt(t.happened_at)}</span>
                    <span><Badge>{ACTIVITY_LABEL[t.type]}</Badge> {t.contact_name && <span className="text-slate-500">with {t.contact_name}</span>} {t.notes}</span></li>
                ))}
              </ul>
            </Card>
          </div>
          <div className="space-y-4">
            <AgencyInfo agency={a} onSave={patch} />
            <Contacts agencyId={a.id} contacts={bundle.contacts} onChange={refresh} />
            <Card title={`Their live ads (${bundle.live_ads.length})`}>
              {bundle.live_ads.length === 0 ? <p className="text-sm text-slate-500">None seen in the last 14 days.</p> : (
                <ul className="space-y-1 text-sm">{bundle.live_ads.slice(0, 15).map((l: any) => (
                  <li key={l.job_id}><a href={l.url} target="_blank" rel="noreferrer">{l.title}</a> <span className="text-slate-500">{l.location_name}</span></li>))}</ul>
              )}
            </Card>
          </div>
        </div>
      )}
      {dialog}
      {convert && <PilotDialog agency={a} plan="done_for_you" onClose={() => setConvert(false)} onDone={() => { setConvert(false); refresh(); }} />}
    </>
  );
}

function AgencyInfo({ agency, onSave }: { agency: any; onSave: (b: any) => Promise<void> }) {
  const [f, setF] = useState({ website: agency.website || "", next_follow_up: agency.next_follow_up || "", notes: agency.notes || "" });
  const [saved, setSaved] = useState(false);
  async function save() {
    await onSave({ website: f.website || undefined, notes: f.notes, ...(f.next_follow_up ? { next_follow_up: f.next_follow_up } : { clear_follow_up: true }) });
    setSaved(true); setTimeout(() => setSaved(false), 1200);
  }
  return (
    <Card title="Details">
      <div className="space-y-2">
        <div><Label>Website</Label><Input value={f.website} onChange={(e) => setF({ ...f, website: e.target.value })} placeholder="https://…" />
          {agency.website && <a href={agency.website} target="_blank" rel="noreferrer" className="text-xs">open</a>}</div>
        <div><Label>Next follow-up</Label><Input type="date" value={f.next_follow_up} onChange={(e) => setF({ ...f, next_follow_up: e.target.value })} data-testid="follow-up" /></div>
        <div><Label>Notes</Label><Textarea rows={3} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></div>
        <Button size="sm" onClick={save}>{saved ? "Saved ✓" : "Save"}</Button>
      </div>
    </Card>
  );
}

function Contacts({ agencyId, contacts, onChange }: { agencyId: number; contacts: any[]; onChange: () => void }) {
  const [adding, setAdding] = useState(false);
  const [edit, setEdit] = useState<any>(null);
  const [f, setF] = useState({ full_name: "", role: "", email: "", linkedin_url: "" });
  const [err, setErr] = useState<string>();
  async function add(e: React.FormEvent) {
    e.preventDefault(); setErr(undefined);
    try { await api(`/agencies/${agencyId}/contacts`, { body: { ...f, email: f.email || null, linkedin_url: f.linkedin_url || null } });
      setAdding(false); setF({ full_name: "", role: "", email: "", linkedin_url: "" }); onChange(); } catch (e: any) { setErr(e.message); }
  }
  async function patch(id: number, body: any) {
    try { await api(`/contacts/${id}`, { method: "PATCH", body }); setEdit(null); onChange(); } catch (e: any) { alert(e.message); }
  }
  async function forget(c: any) {
    if (!confirm(`Forget ${c.full_name}? Their details are deleted and their email can never be contacted again.`)) return;
    await api(`/contacts/${c.id}/forget`, { method: "POST" }); onChange();
  }
  return (
    <Card title="Contacts" action={<Button size="sm" variant="ghost" onClick={() => setAdding(!adding)}>Add</Button>}>
      {adding && (
        <form onSubmit={add} className="mb-3 space-y-2">
          <Input required placeholder="Full name" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} />
          <Input placeholder="Role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} />
          <Input type="email" placeholder="Business email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
          <Input placeholder="LinkedIn URL" value={f.linkedin_url} onChange={(e) => setF({ ...f, linkedin_url: e.target.value })} />
          <ErrorNote error={err} /><Button size="sm">Save contact</Button>
        </form>
      )}
      {contacts.length === 0 && <p className="text-sm text-slate-500">No contacts. Run Companies House to add directors.</p>}
      <ul className="space-y-3">
        {contacts.map((c) => (
          <li key={c.id} className={cn("text-sm", c.do_not_contact && "opacity-60")}>
            {edit?.id === c.id ? (
              <div className="space-y-1.5">
                <Input type="email" placeholder="Email" value={edit.email || ""} onChange={(e) => setEdit({ ...edit, email: e.target.value })} />
                <Input placeholder="LinkedIn URL" value={edit.linkedin_url || ""} onChange={(e) => setEdit({ ...edit, linkedin_url: e.target.value })} />
                <label className="flex items-center gap-2"><input type="checkbox" checked={!!edit.email_verified} onChange={(e) => setEdit({ ...edit, email_verified: e.target.checked })} /> Email verified</label>
                <div className="flex gap-1"><Button size="sm" onClick={() => patch(c.id, { email: edit.email || null, linkedin_url: edit.linkedin_url || null, email_verified: !!edit.email_verified })}>Save</Button>
                  <Button size="sm" variant="ghost" onClick={() => setEdit(null)}>Cancel</Button></div>
              </div>
            ) : (
              <>
                <p className="font-medium">{c.full_name} {c.role && <span className="font-normal text-slate-500">· {c.role}</span>} {c.do_not_contact && <Badge tone="red">do not contact</Badge>}</p>
                <p className="text-slate-500">{c.email ? <>{c.email} {c.email_verified ? <Badge tone="green">verified</Badge> : <Badge tone="amber">{c.email_status || "unverified"}</Badge>}</> : "no email"}
                  {c.linkedin_url && <> · <a href={c.linkedin_url} target="_blank" rel="noreferrer">LinkedIn</a></>}</p>
                <div className="mt-1 flex flex-wrap gap-1">
                  <Button size="sm" variant="ghost" onClick={() => setEdit(c)}>Edit</Button>
                  {!c.do_not_contact && <Button size="sm" variant="ghost" onClick={() => patch(c.id, { do_not_contact: true })}>Do not contact</Button>}
                  <Button size="sm" variant="ghost" className="text-red-600" onClick={() => forget(c)}>Forget</Button>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function DraftPanel({ agency, templates, contacts, onLogged }: { agency: any; templates: any[]; contacts: any[]; onLogged: () => void }) {
  const usable = templates.filter((t) => t.channel !== "client_hm_email");
  const [tid, setTid] = useState<number | undefined>(usable[0]?.id);
  const [cid, setCid] = useState<number | undefined>(contacts.find((c) => !c.do_not_contact)?.id);
  const [draft, setDraft] = useState<any>(null);
  const [copied, setCopied] = useState(false);
  const [logged, setLogged] = useState(false);
  useEffect(() => {
    if (!tid) return;
    api(`/agencies/${agency.id}/draft?template_id=${tid}${cid ? `&contact_id=${cid}` : ""}`, { method: "POST" }).then(setDraft).catch((e) => setDraft({ blocked: e.message }));
  }, [tid, cid, agency.id]);
  const tmpl = templates.find((t) => t.id === tid);
  async function copy() {
    const text = draft.subject ? `Subject: ${draft.subject}\n\n${draft.body}` : draft.body;
    await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500);
  }
  async function logSent() {
    const type = tmpl?.channel === "linkedin" ? "linkedin_dm" : "email";
    await api("/activities", { body: { agency_id: agency.id, type, contact_id: cid, notes: `Sent: ${tmpl?.name}` } });
    if (agency.stage === "new" || agency.stage === "researching") await api(`/agencies/${agency.id}`, { method: "PATCH", body: { stage: "contacted" } });
    setLogged(true); setTimeout(() => setLogged(false), 1500); onLogged();
  }
  return (
    <Card title="Draft" className="scroll-mt-20" action={<span id="draft" />}>
      <div className="mb-3 grid gap-2 sm:grid-cols-2">
        <Select value={tid} onChange={(e) => setTid(Number(e.target.value))} aria-label="Template">{usable.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</Select>
        <Select value={cid} onChange={(e) => setCid(Number(e.target.value))} aria-label="Contact">
          {contacts.filter((c) => !c.do_not_contact).map((c) => <option key={c.id} value={c.id}>{c.full_name}</option>)}
          {contacts.filter((c) => !c.do_not_contact).length === 0 && <option value="">No contact</option>}</Select>
      </div>
      {!draft ? <p className="text-sm text-slate-500">Loading…</p> : draft.blocked ? <ErrorNote error={`No draft: ${draft.blocked}`} /> : (
        <>
          {!agency.email_allowed && tmpl?.channel === "email" && <p className="mb-2 rounded-lg bg-amber-50 p-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-300">{agency.compliance.charAt(0).toUpperCase() + agency.compliance.slice(1)}.</p>}
          {draft.missing.length > 0 && <p className="mb-2 rounded-lg bg-red-50 p-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300" role="alert">Missing: {draft.missing.join(", ")}. Don't send this one yet.</p>}
          {draft.subject && <p className="mb-1 text-sm"><span className="text-slate-500">Subject:</span> {draft.subject}</p>}
          <pre className="whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-sans text-sm dark:bg-slate-950" data-testid="draft-body">{draft.body}</pre>
          <div className="mt-2 flex gap-2"><Button onClick={copy} variant="secondary">{copied ? "Copied ✓" : "Copy"}</Button>
            <Button onClick={logSent} data-testid="log-sent">{logged ? "Logged ✓" : "Log as sent"}</Button></div>
        </>
      )}
    </Card>
  );
}
