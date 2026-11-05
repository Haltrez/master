"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote } from "@/components/ui";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";

export const usd = (v: any) => "$" + Number(v || 0).toFixed(2);

export const TONE: Record<string, "blue" | "green" | "red" | "slate" | "amber"> = {
  queued: "slate", running: "blue", done: "green", error: "red", cancelled: "amber" };

// Paste a research brief ("build a hot-lead list for this agency...") and the agent does the job.
export function ProjectsClient({ initial }: { initial: any[] }) {
  const router = useRouter();
  const [brief, setBrief] = useState("");
  const [err, setErr] = useState<string>();
  const [busy, setBusy] = useState(false);
  async function start() {
    setErr(undefined); setBusy(true);
    try {
      const p = await api<any>("/projects", { body: { brief } });
      router.push(`/projects/${p.id}`);
    } catch (e: any) { setErr(e.message); setBusy(false); }
  }
  return (
    <>
      <Card title="New project" className="mb-4">
        <p className="mb-2 text-sm text-slate-500">Paste the whole brief: client, roles, radiuses, sources, hard rules and the outputs you want.
          The agent reads careers pages, searches the job boards, checks the web archive, Companies House, distances and every link,
          then hands back the sheet, the email and a note for you. Usually 10-30 minutes.</p>
        <textarea value={brief} onChange={(e) => setBrief(e.target.value)} rows={10} aria-label="Project brief"
          placeholder={"TASK: Build a hot-lead list for a prospect agency...\nCLIENT ...\nCANDIDATE BRIEFS ...\nHARD RULES ...\nOUTPUT 1 ..."}
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20 dark:border-slate-700 dark:bg-slate-950" />
        <div className="mt-2 flex items-center gap-2">
          <Button disabled={busy || brief.trim().length < 20} onClick={start}>{busy ? "Starting…" : "Start project"}</Button>
          <ErrorNote error={err} />
        </div>
      </Card>
      <Card title={`Projects (${initial.length})`}>
        {initial.length === 0 ? <Empty>No projects yet.</Empty> : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {initial.map((p) => (
              <li key={p.id} className="py-2.5 text-sm">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <Link href={`/projects/${p.id}`} className="font-medium">{p.title}</Link>
                  <Badge tone={TONE[p.status]}>{p.status}</Badge>
                </div>
                <p className="truncate text-xs text-slate-500">{p.steps} steps · {usd(p.cost_usd)} · {ago(p.created_at)}{p.last_step?.text && <> · {p.last_step.text}</>}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
