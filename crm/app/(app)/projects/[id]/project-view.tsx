"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge, Button, Card, ErrorNote, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { TONE } from "../projects-client";

const isUrl = (v: string) => /^https?:\/\//.test(v || "");

export function ProjectView({ initial }: { initial: any }) {
  const [p, setP] = useState<any>(initial);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState<string>();
  const [copied, setCopied] = useState(false);
  const running = p.status === "running" || p.status === "queued";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => api<any>(`/projects/${p.id}`).then(setP).catch(() => {}), 4000);
    return () => clearInterval(t);
  }, [running, p.id]);
  const r = p.result;
  async function send() {
    setErr(undefined);
    try { setP(await api(`/projects/${p.id}/message`, { body: { message: msg } })); setMsg(""); } catch (e: any) { setErr(e.message); }
  }
  const email = r?.email_body ? `${r.email_subject ? `Subject: ${r.email_subject}\n\n` : ""}${r.email_body}` : "";
  return (
    <>
      <PageHeader title={p.title}>
        <Badge tone={TONE[p.status]}>{p.status}</Badge>
        {running && <Button size="sm" variant="ghost" onClick={async () => setP(await api(`/projects/${p.id}/cancel`, { method: "POST" }))}>Cancel</Button>}
        <Link href="/projects" className="text-sm">All projects</Link>
      </PageHeader>
      {p.error && <div className="mb-3"><ErrorNote error={p.error} /></div>}

      {r && (
        <Card title={`Results: ${r.rows?.length || 0} rows`} className="mb-4"
          action={<a href={`/api/engine/projects/${p.id}/result.csv`} className="text-sm font-medium">Download CSV</a>}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left uppercase tracking-wide text-slate-500">
                <tr>{(r.columns || []).map((c: string) => <th key={c} className="py-1.5 pr-3 align-bottom">{c}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {(r.rows || []).map((row: string[], i: number) => (
                  <tr key={i} className="align-top">{row.map((v, j) => (
                    <td key={j} className="max-w-xs py-1.5 pr-3">{isUrl(v) ? <a href={v} target="_blank" rel="noreferrer">link</a> : v}</td>
                  ))}</tr>
                ))}
              </tbody>
            </table>
          </div>
          {r.summary_lines?.length > 0 && <ul className="mt-3 space-y-0.5 text-xs text-slate-600 dark:text-slate-400">
            {r.summary_lines.map((l: string, i: number) => <li key={i}>{l}</li>)}</ul>}
        </Card>
      )}
      {r && (
        <div className="mb-4 grid gap-4 lg:grid-cols-2">
          {email && <Card title="Email" action={<button className="text-sm" onClick={() => { navigator.clipboard?.writeText(email); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>{copied ? "Copied ✓" : "Copy"}</button>}>
            <pre className="whitespace-pre-wrap font-sans text-sm">{email}</pre></Card>}
          {r.note && <Card title="Note for you"><pre className="whitespace-pre-wrap font-sans text-sm">{r.note}</pre></Card>}
        </div>
      )}

      {!running && (
        <Card title="Ask for changes" className="mb-4">
          <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={3} aria-label="Follow-up message"
            placeholder='e.g. "add Bolton and Wigan for the site manager brief" or "drop Kier, they use a preferred supplier list"'
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950" />
          <div className="mt-2 flex items-center gap-2"><Button size="sm" onClick={send}>{p.status === "done" ? "Send" : "Continue"}</Button><ErrorNote error={err} /></div>
        </Card>
      )}

      <Card title={`What it did (${p.steps} steps)`}>
        <ol className="space-y-1 text-xs" data-testid="project-log">
          {[...(p.log || [])].reverse().map((l: any, i: number) => (
            <li key={i} className="flex gap-2"><span className="shrink-0 tabular-nums text-slate-400">{(l.at || "").slice(11, 16)}</span><span>{l.text}</span></li>
          ))}
        </ol>
        {running && <p className="mt-2 text-xs text-slate-500">Working… this page updates by itself.</p>}
        <details className="mt-3 text-xs"><summary className="cursor-pointer text-slate-500">The brief</summary>
          <pre className="mt-2 whitespace-pre-wrap font-sans">{p.brief}</pre></details>
      </Card>
    </>
  );
}
