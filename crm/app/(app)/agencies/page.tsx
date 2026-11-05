import Link from "next/link";
import { Badge, Card, Empty, ErrorNote, PageHeader } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { CrmList } from "./crm-list";

export const dynamic = "force-dynamic";

const STATUS: Record<string, string> = { collecting: "searching", waiting: "topping up", done: "complete", paused: "paused", error: "problem" };

// Two lists: agencies you sell to (their orders and results), and agencies advertising in your
// territories that you could sell to next.
export default async function AgenciesPage() {
  const { data, error } = await engineOr<any>("/agencies-overview", { clients: [], prospects: [] });
  return (
    <>
      <PageHeader title="Agencies" />
      <ErrorNote error={error} />
      <Card title={`Your clients (${data.clients.length})`} className="mb-4">
        {data.clients.length === 0 ? <Empty>No clients yet: an agency appears here when you make its first order.</Empty> : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {data.clients.map((a: any) => (
              <li key={a.id} className="py-3 text-sm">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="font-medium">{a.name} {a.email && <span className="font-normal text-slate-500">· {a.email}</span>}</p>
                  <p className="text-xs text-slate-500">{a.leads_sent} leads sent · {a.contacted} contacted · {a.meetings} meetings · {a.placements} placements</p>
                </div>
                <CrmList agency={a} />
                <ul className="mt-1 space-y-0.5 text-xs">
                  {a.orders.map((o: any) => (
                    <li key={o.id}>
                      <Link href={`/leads?order=${o.id}`}>{o.industry} · {o.location}</Link>{" "}
                      <span className="text-slate-500">{o.sent}/{o.leads_wanted}{o.frequency === "weekly" ? " a week" : ""} · {o.package === "outreach" ? "sheet + outreach" : "sheet"} · {STATUS[o.status] || o.status}</span>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title={`Agencies to sell to (${data.prospects.length})`}>
        <p className="mb-3 text-sm text-slate-500">Recruitment agencies advertising jobs in your territories in the last 90 days, most active first.
          They already recruit in that industry and area, so they're the ones who'd buy those leads.</p>
        {data.prospects.length === 0 ? <Empty>None yet: they appear once your territories have collected ads.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                <tr><th className="py-2 pr-3">Agency</th><th className="pr-3">Ads (90 days)</th><th className="pr-3">Territories</th>
                  <th className="pr-3">Contact</th><th>Company</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {data.prospects.map((a: any) => (
                  <tr key={a.id} className="align-top">
                    <td className="py-2 pr-3"><Link href={`/agencies/${a.id}`} className="font-medium">{a.name}</Link>
                      {a.website && <div className="text-xs"><a href={a.website} target="_blank" rel="noreferrer">{a.website.replace(/^https?:\/\//, "")}</a></div>}</td>
                    <td className="pr-3 tabular-nums">{a.ads}</td>
                    <td className="pr-3 text-xs">{a.territories}</td>
                    <td className="pr-3 text-xs">{a.contact || <span className="text-slate-400">–</span>}{a.email && <div>{a.email}</div>}</td>
                    <td className="text-xs">{a.ch_company_type && a.ch_company_type !== "not_found" && a.ch_company_type !== "needs_review"
                      ? <Badge tone="green">{a.ch_company_type.toUpperCase()}</Badge> : <span className="text-slate-400">not checked</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
