import { ErrorNote, PageHeader } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { HotLeadsClient } from "./hot-leads-client";

export const dynamic = "force-dynamic";

export default async function HotLeads({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const qs = new URLSearchParams();
  if (sp.search_id) qs.set("search_id", sp.search_id);
  if (sp.min_score) qs.set("min_score", sp.min_score);
  if (sp.status) qs.set("status", sp.status);
  if (sp.live === "1") qs.set("live_only", "true");
  const [leads, searches] = await Promise.all([engineOr<any[]>(`/hot-leads?${qs}`, []), engineOr<any[]>("/searches", [])]);
  return (
    <>
      <PageHeader title="Hot leads" />
      <p className="-mt-3 mb-4 text-sm text-slate-500">Companies posting the same role again and again, or keeping one ad open for 30+ days: they can't fill it.</p>
      <ErrorNote error={leads.error} />
      <HotLeadsClient leads={leads.data} searches={searches.data} params={sp} />
    </>
  );
}
