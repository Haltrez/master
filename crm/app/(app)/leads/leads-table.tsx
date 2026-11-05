"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Card, Empty, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { d } from "@/lib/format";

const OUTCOMES = [["none", "–"], ["contacted", "Contacted"], ["meeting", "Meeting"], ["placement", "Placement"]];

// Every lead that went to an agency, exactly as it was in their sheet. Set what happened with each one
// (for the outreach package, this is the report back to the agency).
export function LeadsTable({ orders, leads, orderId }: { orders: any[]; leads: any[]; orderId?: number }) {
  const router = useRouter();
  const [open, setOpen] = useState<number | null>(null);
  const [outcome, setOutcome] = useState<Record<number, string>>({});
  const counts = leads.reduce((c: Record<string, number>, l) => { const o = outcome[l.id] || l.outcome; c[o] = (c[o] || 0) + 1; return c; }, {});
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select className="w-auto" value={orderId || ""} aria-label="Order"
          onChange={(e) => router.push(e.target.value ? `/leads?order=${e.target.value}` : "/leads")}>
          <option value="">All orders</option>
          {orders.map((o) => <option key={o.id} value={o.id}>{o.agency_name} · {o.industry} · {o.location}</option>)}
        </Select>
        {orderId && <><a href={`/api/engine/orders/${orderId}/leads.xlsx`} className="text-sm font-medium">Download Excel</a>
          <a href={`/api/engine/orders/${orderId}/leads.csv`} className="text-sm text-slate-500">CSV</a></>}
        <span className="ml-auto text-sm text-slate-500">{leads.length} sent · {counts.contacted || 0} contacted ·{" "}
          {counts.meeting || 0} meetings · {counts.placement || 0} placements</span>
      </div>
      {leads.length === 0 ? <Empty>No leads sent yet.</Empty> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr><th className="py-2 pr-3">Company</th><th className="pr-3">Role</th><th className="pr-3">Posted</th>
                <th className="pr-3">Location</th><th className="pr-3">Decision maker</th><th className="pr-3">Agency</th>
                <th className="pr-3">Sent</th><th>Result</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {leads.map((l) => {
                const r = l.row || {};
                return [
                  <tr key={l.id} className="cursor-pointer align-top hover:bg-slate-50 dark:hover:bg-slate-900" onClick={() => setOpen(open === l.id ? null : l.id)}>
                    <td className="py-2 pr-3 font-medium">{r["Company"]}</td>
                    <td className="pr-3">{r["Role"]}{r["Also hiring"] && <div className="text-xs text-slate-500">also: {r["Also hiring"]}</div>}</td>
                    <td className="pr-3 tabular-nums">{r["Times posted"]}×</td>
                    <td className="pr-3">{r["Location"]}</td>
                    <td className="pr-3">{r["Decision maker"] || <span className="text-slate-400">–</span>}
                      {r["Email"] && <div className="text-xs text-slate-500">{r["Email"]}</div>}</td>
                    <td className="pr-3 text-xs">{l.agency_name}<div className="text-slate-500">{l.industry}</div></td>
                    <td className="pr-3 text-xs">{d(l.delivered_at)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <Select className="w-auto py-1" value={outcome[l.id] || l.outcome} aria-label={`Result for ${r["Company"]}`}
                        onChange={async (e) => { const v = e.target.value; setOutcome((o) => ({ ...o, [l.id]: v }));
                          await api(`/deliveries/${l.id}`, { method: "PATCH", body: { outcome: v } }); }}>
                        {OUTCOMES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                      </Select>
                    </td>
                  </tr>,
                  open === l.id && (
                    <tr key={`${l.id}-x`}><td colSpan={8} className="bg-slate-50 p-3 text-xs dark:bg-slate-900">
                      <p className="mb-1"><b>Why it's a lead:</b> {r["Why it's a lead"]}</p>
                      <p className="mb-1">{r["Salary"] && <>Salary {r["Salary"]} · </>}Posted on {r["Posted on"]} ·{" "}
                        {r["Ad link"] && <a href={r["Ad link"]} target="_blank" rel="noreferrer">ad</a>}
                        {r["Previous ad"] && <> · <a href={r["Previous ad"]} target="_blank" rel="noreferrer">previous ad</a></>}</p>
                      {r["Suggested email"] && <pre className="mt-2 whitespace-pre-wrap font-sans">{r["Suggested email"]}</pre>}
                    </td></tr>
                  ),
                ];
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
