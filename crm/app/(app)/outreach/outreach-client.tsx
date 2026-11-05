"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Modal } from "@/components/modal";
import { Badge, Button, Card, Empty, ErrorNote, cn } from "@/components/ui";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";

const pct = (v: number) => `${((v || 0) * 100).toFixed(1)}%`;
const CATS = [["interested", "Interested"], ["not_now", "Not now"], ["not_interested", "Not interested"],
  ["unsubscribe", "Unsubscribe"], ["out_of_office", "Out of office"], ["wrong_person", "Wrong person"]] as const;

export function OutreachClient({ settings }: { settings: any }) {
  return (
    <div className="space-y-4">
      <Stats />
      <Queue settings={settings} />
      <Replies />
      <Campaigns />
      <Health />
    </div>
  );
}

function useLoad<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string>();
  const [busy, setBusy] = useState(false);
  const load = useCallback(async (suffix = "") => {
    setBusy(true); setErr(undefined);
    try { setData(await api<T>(path + suffix)); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }, [path]);
  useEffect(() => { load(); }, [load]);
  return { data, err, busy, load, setData, setErr };
}

function Stats() {
  const { data, err, busy, load } = useLoad<any>("/outreach/stats");
  const Box = ({ label, v, red }: { label: string; v: any; red?: boolean }) => (
    <div className="rounded-xl border border-slate-200 p-3 dark:border-slate-800">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className={cn("text-lg font-semibold tabular-nums", red && "text-red-600")}>{v}</p></div>
  );
  return (
    <Card title="Today / this week" action={<Button size="sm" variant="secondary" disabled={busy} onClick={() => load("?refresh=true")}>{busy ? "Refreshing…" : "Refresh"}</Button>}>
      <ErrorNote error={err} />
      {data && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8" data-testid="outreach-stats">
            {([["Queued", "queued"], ["Sent", "sent"], ["Replies", "replies"], ["Positive", "positive_replies"], ["Bounces", "bounces"],
              ["Unsubscribes", "unsubscribes"]] as const).map(([l, k]) => <Box key={k} label={l} v={`${data.today?.[k] ?? 0} / ${data.week?.[k] ?? 0}`} />)}
            <Box label="Bounce rate" v={`${pct(data.today?.bounce_rate)} / ${pct(data.week?.bounce_rate)}`} red={data.today?.bounce_rate > 0.02 || data.week?.bounce_rate > 0.02} />
            <Box label="Reply rate" v={`${pct(data.today?.reply_rate)} / ${pct(data.week?.reply_rate)}`} />
          </div>
          <p className="mt-2 text-xs text-slate-500">From Smartlead campaign stats, refreshed every 15 minutes · updated {ago(data.at)}</p>
        </>
      )}
    </Card>
  );
}

function Queue({ settings }: { settings: any }) {
  const { data: rows, err, busy, load, setData, setErr } = useLoad<any[]>("/outreach/queue");
  const [preview, setPreview] = useState<any>(null);
  const [results, setResults] = useState<Record<number, any>>({});
  const [skip, setSkip] = useState<Record<number, boolean>>({});
  const [working, setWorking] = useState("");
  const [test, setTest] = useState<any>(null);
  async function build() {
    setWorking("build"); setErr(undefined); setResults({});
    try { setData(await api<any[]>("/outreach/queue/build", { method: "POST" })); } catch (e: any) { setErr(e.message); } finally { setWorking(""); }
  }
  async function act(id: number, action: string, which = 1) {
    try { await api(`/outreach/queue/${id}`, { body: { action, which } }); await load(); } catch (e: any) { setErr(e.message); }
  }
  const pushable = (rows || []).filter((r) => r.status === "built" && !skip[r.id]);
  async function push() {
    if (!confirm(`Push ${pushable.length} leads to Smartlead?`)) return;
    setWorking("push"); setErr(undefined);
    try {
      const res = await api<any[]>("/outreach/queue/push", { body: { ids: pushable.map((r) => r.id) } });
      setResults(Object.fromEntries(res.map((x) => [x.id, x]))); await load();
    } catch (e: any) { setErr(e.message); } finally { setWorking(""); }
  }
  async function testPush() {
    setWorking("test"); setErr(undefined);
    try { setTest(await api("/outreach/queue/test-push", { method: "POST" })); } catch (e: any) { setErr(e.message); } finally { setWorking(""); }
  }
  const L = (r: any, i: number) => r.fields[`lead${i}_employer`]
    ? <span>{r.fields[`lead${i}_employer`]}<span className="text-slate-500"> · {r.fields[`lead${i}_role`]} · {r.fields[`lead${i}_count`]}× since {r.fields[`lead${i}_since`]}</span></span>
    : <span className="text-slate-400">–</span>;
  return (
    <Card title={`Today's queue (${(rows || []).filter((r) => r.status === "built").length} ready, limit ${settings.daily_push_limit})`}
      action={<div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" disabled={!!working} onClick={build}>{working === "build" ? "Building…" : "Build queue"}</Button>
        <Button size="sm" variant="secondary" disabled={!!working || !settings.owner_test_email} onClick={testPush}
          title={settings.owner_test_email ? `One lead to ${settings.owner_test_email}` : "Set OWNER_TEST_EMAIL on the server"}>
          {working === "test" ? "Sending…" : "Test push"}</Button>
        <Button size="sm" disabled={!!working || !pushable.length} onClick={push}>{working === "push" ? "Pushing…" : `Push ${pushable.length} to Smartlead`}</Button>
      </div>}>
      <p className="mb-2 text-xs text-slate-500">Auto-push at 07:30 UK is <b>{settings.auto_push ? "on" : "off"}</b>. Blocked rows are never pushed.
        Click a row to read the email exactly as it will go out.</p>
      <ErrorNote error={err} />
      {test && <p className={cn("mb-2 rounded-lg p-2 text-sm", test.ok ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300" : "bg-red-50 text-red-700")}>
        {test.ok ? `Test lead sent to ${test.email} in campaign ${test.campaign_id}: check your inbox when the campaign sends.` : `Test push failed: ${test.error}`}</p>}
      {busy && !rows ? <p className="text-sm text-slate-500">Loading…</p> : !rows?.length ? <Empty>No queue for today yet: press Build queue.</Empty> : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left uppercase tracking-wide text-slate-500"><tr>
              <th className="py-1.5 pr-2"></th><th className="pr-2">Agency</th><th className="pr-2">Contact</th><th className="pr-2">Niche · City</th>
              <th className="pr-2">Lead 1</th><th className="pr-2">Lead 2</th><th className="pr-2">Campaign</th><th className="pr-2">Warnings</th><th></th></tr></thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {rows.map((r) => {
                const res = results[r.id];
                const blocked = r.status === "blocked";
                return (
                  <tr key={r.id} className={cn("cursor-pointer align-top hover:bg-slate-50 dark:hover:bg-slate-900", blocked && "opacity-60")} data-testid="queue-row"
                    onClick={async () => setPreview({ row: r, ...(await api(`/outreach/queue/${r.id}/preview`)) })}>
                    <td className="py-1.5 pr-2" onClick={(e) => e.stopPropagation()}>
                      {r.status === "built" && <input type="checkbox" checked={!skip[r.id]} aria-label={`Approve ${r.agency_name}`}
                        onChange={(e) => setSkip((s) => ({ ...s, [r.id]: !e.target.checked }))} />}</td>
                    <td className="pr-2 font-medium">{r.agency_name}
                      <div>{r.status === "pushed" ? <Badge tone="green">pushed</Badge> : blocked ? <Badge tone="red">blocked</Badge>
                        : r.status === "failed" ? <Badge tone="red">failed</Badge> : null}</div></td>
                    <td className="pr-2">{r.fields._contact_name}<div className="text-slate-500">{r.fields._email}</div></td>
                    <td className="pr-2">{r.fields.niche}<div className="text-slate-500">{r.fields.city}</div></td>
                    <td className="pr-2">{L(r, 1)}</td><td className="pr-2">{L(r, 2)}</td>
                    <td className="pr-2">{r.campaign_role}</td>
                    <td className="max-w-[14rem] pr-2 text-amber-700 dark:text-amber-400">{(r.warnings || []).join("; ")}
                      {(res?.error || r.error) && <div className="text-red-600">{res?.error || r.error}</div>}</td>
                    <td onClick={(e) => e.stopPropagation()} className="whitespace-nowrap">
                      {(r.status === "built" || r.status === "failed") && <>
                        <button className="mr-2 text-brand" onClick={() => act(r.id, "swap", 1)}>Swap lead</button>
                        <button className="mr-2 text-brand" onClick={() => act(r.id, r.campaign_role === "main" ? "fallback" : "main")}>
                          {r.campaign_role === "main" ? "To fallback" : "To main"}</button></>}
                      {r.status !== "pushed" && <button className="text-red-600" onClick={() => act(r.id, "remove")}>Remove</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {preview && (
        <Modal title={`${preview.row.agency_name}: the email`} onClose={() => setPreview(null)}>
          {preview.note && <p className="mb-2 text-xs text-amber-700">{preview.note}</p>}
          <p className="mb-2 text-xs text-slate-500">To {preview.row.fields._email} · {preview.row.campaign_role} campaign {preview.campaign_id || "(not set)"}</p>
          {(preview.steps || []).map((s: any) => (
            <div key={s.seq_number} className="mb-3 rounded-lg border border-slate-200 p-3 text-sm dark:border-slate-800">
              <p className="text-xs text-slate-500">Email {s.seq_number}</p>
              <p className="font-medium">{s.subject || <span className="text-slate-400">(same thread)</span>}</p>
              <pre className="mt-1 whitespace-pre-wrap font-sans">{s.body}</pre></div>
          ))}
        </Modal>
      )}
    </Card>
  );
}

function Replies() {
  const { data, err, load, setErr } = useLoad<any[]>("/outreach/replies");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Record<number, string>>({});
  async function sync() {
    setBusy(true); setErr(undefined);
    try { await api("/outreach/replies/sync", { method: "POST" }); await load(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  async function cat(id: number, c: string) {
    if ((c === "unsubscribe" || c === "not_interested") && !confirm("Mark do-not-contact, add to suppression and unsubscribe from every Smartlead campaign?")) return;
    try { const r = await api<any>(`/outreach/replies/${id}/categorise`, { body: { category: c } });
      setDone((d) => ({ ...d, [id]: [...r.crm, ...r.smartlead].join(" · ") })); await load(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <Card title={`Replies (${data?.length || 0})`} action={<Button size="sm" variant="secondary" disabled={busy} onClick={sync}>{busy ? "Syncing…" : "Sync now"}</Button>}>
      <p className="mb-2 text-xs text-slate-500">Synced from Smartlead every 15 minutes. Reply in the inbox; categorise here.</p>
      <ErrorNote error={err} />
      {!data?.length ? <Empty>No replies yet.</Empty> : (
        <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {data.map((r) => (
            <li key={r.id} className="py-2" data-testid="reply">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p><b>{r.agency_name || r.lead_email}</b> <span className="text-slate-500">· {r.contact_name || r.lead_email} · {ago(r.received_at)}</span></p>
                <span className="text-xs">{r.category ? <Badge tone={r.category === "interested" ? "green" : "slate"}>{r.category.replace("_", " ")}</Badge>
                  : r.smartlead_category ? <Badge>{r.smartlead_category}</Badge> : null}</span>
              </div>
              <p className="text-xs text-slate-600 dark:text-slate-400">{r.subject && <b>{r.subject}: </b>}{r.snippet}</p>
              <div className="mt-1 flex flex-wrap gap-1">
                {CATS.map(([c, label]) => <Button key={c} size="sm" variant={r.category === c ? "primary" : "ghost"} onClick={() => cat(r.id, c)}>{label}</Button>)}
                <a className="self-center text-xs" target="_blank" rel="noreferrer" href="https://app.smartlead.ai/app/master-inbox">Open thread in Smartlead ↗</a>
              </div>
              {done[r.id] && <p className="mt-1 text-xs text-emerald-700">{done[r.id]}</p>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Campaigns() {
  const { data, err, load, setErr } = useLoad<any[]>("/outreach/campaigns");
  async function status(id: number, what: "pause" | "resume") {
    try { await api(`/outreach/campaigns/${id}/${what}`, { method: "POST" }); await load(); } catch (e: any) { setErr(e.message); }
  }
  return (
    <Card title="Campaigns" action={<Link href="/settings/outreach" className="text-sm">Set main / fallback</Link>}>
      <ErrorNote error={err} />
      {!data ? <p className="text-sm text-slate-500">Loading…</p> : !data.length ? <Empty>No campaigns in Smartlead yet.</Empty> : (
        <div className="overflow-x-auto"><table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500"><tr><th className="py-1.5">Campaign</th><th>Status</th><th>Inboxes</th>
            <th>Daily limit</th><th>Sent (30d)</th><th>Replies (30d)</th><th></th></tr></thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {data.map((c) => (
              <tr key={c.id}>
                <td className="py-1.5">{c.name} {c.role && <Badge tone={c.role === "main" ? "blue" : "amber"}>{c.role.toUpperCase()}</Badge>}</td>
                <td><Badge tone={c.status === "ACTIVE" ? "green" : "slate"}>{c.status}</Badge></td>
                <td className="tabular-nums">{c.inboxes ?? "?"}</td><td className="tabular-nums">{c.daily_limit ?? "?"}</td>
                <td className="tabular-nums">{c.sent_30d}</td><td className="tabular-nums">{c.replies_30d}</td>
                <td className="whitespace-nowrap text-xs">
                  {c.status === "ACTIVE" ? <button className="mr-2 text-brand" onClick={() => status(c.id, "pause")}>Pause</button>
                    : c.status === "PAUSED" ? <button className="mr-2 text-brand" onClick={() => status(c.id, "resume")}>Resume</button> : null}
                  <a href={c.url} target="_blank" rel="noreferrer">Open in Smartlead ↗</a></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </Card>
  );
}

function Health() {
  const { data, err, busy, load } = useLoad<any>("/outreach/health");
  return (
    <Card title="Health" action={<Button size="sm" variant="secondary" disabled={busy} onClick={() => load("?refresh=true")}>Check now</Button>}>
      <p className="mb-2 text-xs text-slate-500">A campaign is paused automatically when more than 3% of its last 200 sends bounce (checked every 15 minutes, from 30 sends up).</p>
      <ErrorNote error={err} />
      {data && (
        <>
          {data.campaigns?.filter((c: any) => c.red).map((c: any) => (
            <p key={c.campaign_id} className="mb-2 rounded-lg bg-red-50 p-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
              {c.role} campaign {c.campaign_id}: {pct(c.bounce_rate)} bounced in the last {c.last_sends} sends{c.auto_paused ? ": paused automatically" : ""}.</p>
          ))}
          <div className="overflow-x-auto"><table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500"><tr><th className="py-1.5">Inbox</th><th>Daily limit</th>
              <th>Sent today</th><th>Warmup</th><th>Bounce (30d)</th></tr></thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.inboxes?.map((i: any) => (
                <tr key={i.id} className={i.red ? "text-red-600" : ""}>
                  <td className="py-1.5">{i.email} {!i.connected && <Badge tone="red">disconnected</Badge>}</td>
                  <td className="tabular-nums">{i.daily_limit}</td><td className="tabular-nums">{i.sent_today}</td>
                  <td>{i.warmup_status} · {i.reputation}</td>
                  <td className="tabular-nums">{pct(i.bounce_rate)} <span className="text-xs text-slate-500">({i.bounced_30d}/{i.sent_30d})</span>{i.red && " 🚩"}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
          {data.campaigns?.length > 0 && <p className="mt-2 text-xs text-slate-500">{data.campaigns.map((c: any) =>
            `${c.role}: ${pct(c.bounce_rate)} of last ${c.last_sends} sends bounced`).join(" · ")}</p>}
        </>
      )}
    </Card>
  );
}
