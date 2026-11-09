import { Card, Empty } from "@/components/ui";

// Passive leads: ads that have stayed up without being advertised again (often "evergreen" listings).
// Kept here to keep an eye on; they never go into client sheets or cold emails.
export function WatchList({ leads }: { leads: any[] }) {
  return (
    <Card title={`Watch list (${leads.length})`} className="mt-4">
      <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">Roles that are still advertised but haven't been advertised
        again in the last 30 days. A page that stays up unchanged is often a standing "always recruiting" listing, not real demand,
        so these never go to clients or into cold emails. If one gets re-advertised, it moves to hot leads by itself.</p>
      {leads.length === 0 ? <Empty>Nothing on the watch list.</Empty> : (
        <div className="overflow-x-auto" data-testid="watch-list">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr><th className="py-2 pr-3">Company</th><th className="pr-3">Role</th><th className="pr-3">Location</th>
                <th className="pr-3">Times posted</th><th>Why it's only watched</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {leads.map((l) => (
                <tr key={l.lead_key} className="align-top">
                  <td className="py-2 pr-3 font-medium">{l.employer_name}</td>
                  <td className="pr-3">{l.latest_url ? <a href={l.latest_url} target="_blank" rel="noreferrer">{l.title}</a> : l.title}</td>
                  <td className="pr-3">{l.location_name}</td>
                  <td className="pr-3 tabular-nums">{l.posting_count_90d}</td>
                  <td className="text-slate-600 dark:text-slate-400">{l.evergreen
                    ? <span className="text-amber-700 dark:text-amber-400">Possibly evergreen: {l.evergreen_reason}</span>
                    : "Not advertised again in the last 30 days"}</td>
                </tr>))}
            </tbody>
          </table>
        </div>)}
    </Card>
  );
}
