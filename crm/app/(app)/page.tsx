import Link from "next/link";
import { Badge, Card, Empty, ErrorNote, PageHeader, Progress } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { d, gbp, STAGE_LABEL } from "@/lib/format";
import { QuickLog } from "./quick-log";

export const dynamic = "force-dynamic";

export default async function Today() {
  const { data, error } = await engineOr<any>("/today", null);
  if (!data) return <><PageHeader title="Today" /><ErrorNote error={`Can't reach the engine: ${error}`} /></>;
  const sc = data.scorecard;
  const left = Math.max(0, data.target - sc.today);
  return (
    <>
      <PageHeader title="Today"><span className="text-sm text-slate-500">{d(data.date)}</span></PageHeader>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Outreach today">
          <div className="flex items-end justify-between gap-3">
            <p className="text-5xl font-bold tabular-nums" data-testid="outreach-today">{sc.today}<span className="text-2xl font-medium text-slate-400"> / {data.target}</span></p>
            <p className="text-right text-sm text-slate-500">{left ? `${left} to go` : "Target hit 🎯"}<br />yesterday {sc.yesterday}</p>
          </div>
          <div className="mt-3"><Progress value={sc.today} max={data.target} /></div>
          <div className="mt-4 flex h-12 items-end gap-1" aria-label="Outreach, last 14 days">
            {last14(data.date, data.history).map((h) => (
              <div key={h.day} title={`${d(h.day)}: ${h.n}`} className="flex flex-1 flex-col justify-end">
                <div className={h.n >= data.target ? "rounded-t bg-emerald-500" : "rounded-t bg-brand/60"}
                  style={{ height: `${h.n ? Math.max(3, Math.min(48, (h.n / Math.max(1, data.target)) * 48)) : 2}px` }} />
              </div>
            ))}
          </div>
          <p className="mt-1 text-xs text-slate-500">Last 14 days</p>
        </Card>
        <div className="grid grid-cols-2 gap-4">
          <Stat label="Meetings this week" value={sc.meetings_week} />
          <Stat label="Active pilots" value={sc.active_pilots} />
          <Stat label="Paying clients" value={sc.paying_clients} />
          <Stat label="MRR" value={gbp(sc.mrr_gbp)} />
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" title={`Do next (${data.do_next.length})`}
          action={<Link href="/agencies" className="text-sm">All agencies</Link>}>
          {data.do_next.length === 0 ? (
            <Empty action={<Link href="/searches">Add or run a search</Link>}>
              No agencies ready yet. New agencies appear here once they're confirmed on Companies House and have a director.
            </Empty>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {data.do_next.map((a: any) => (
                <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <div className="min-w-0">
                    <Link href={`/agencies/${a.id}#draft`} className="font-medium">{a.name}</Link>
                    <p className="truncate text-xs text-slate-500">{a.director_name} · {a.niches || "no niche"} · {a.ads_count_90d} {a.ads_count_90d === 1 ? "ad" : "ads"} in 90 days</p>
                  </div>
                  <QuickLog agencyId={a.id} />
                </li>
              ))}
            </ul>
          )}
        </Card>
        <div className="space-y-4">
          <Card title={`Follow-ups due (${data.follow_ups.length})`}>
            {data.follow_ups.length === 0 ? <p className="text-sm text-slate-500">Nothing due today.</p> : (
              <ul className="space-y-1 text-sm">
                {data.follow_ups.map((a: any) => (
                  <li key={a.id} className="flex justify-between gap-2"><Link href={`/agencies/${a.id}`}>{a.name}</Link>
                    <span className="text-slate-500">{d(a.next_follow_up)}</span></li>
                ))}
              </ul>
            )}
          </Card>
          <Card title={`Replies waiting (${data.replies.length})`}>
            {data.replies.length === 0 ? <p className="text-sm text-slate-500">No replies waiting.</p> : (
              <ul className="space-y-1 text-sm">{data.replies.map((a: any) => <li key={a.id}><Link href={`/agencies/${a.id}`}>{a.name}</Link> <Badge>{STAGE_LABEL[a.stage]}</Badge></li>)}</ul>
            )}
          </Card>
          <Card title="New matches for your agencies">
            {data.new_matches.length === 0 ? <p className="text-sm text-slate-500">No new Role Match results.</p> : (
              <ul className="space-y-1 text-sm">{data.new_matches.map((m: any) => (
                <li key={m.agency_id} className="flex justify-between"><Link href={`/agencies/${m.agency_id}#candidates`}>{m.name}</Link><Badge tone="blue">{m.new_matches}</Badge></li>))}
              </ul>
            )}
          </Card>
          <Card title="Hot leads"><p className="text-sm"><span className="text-2xl font-semibold">{data.new_hot_leads}</span> new in the last 24 hours · <Link href="/hot-leads">see all</Link></p></Card>
        </div>
      </div>
    </>
  );
}

function last14(today: string, history: { day: string; outreach_actions: number }[]) {
  const by = new Map(history.map((h) => [h.day, h.outreach_actions]));
  const end = new Date(today + "T12:00:00Z");
  return Array.from({ length: 14 }, (_, i) => {
    const x = new Date(end.getTime() - (13 - i) * 86400_000).toISOString().slice(0, 10);
    return { day: x, n: by.get(x) || 0 };
  });
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Card className="flex flex-col justify-between">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p>
    </Card>
  );
}
