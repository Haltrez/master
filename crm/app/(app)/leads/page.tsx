import { engineOr } from "@/lib/engine";
import { ErrorNote, PageHeader } from "@/components/ui";
import { LeadsTable } from "./leads-table";
import { WatchList } from "./watch-list";

export const dynamic = "force-dynamic";

export default async function LeadsPage({ searchParams }: { searchParams: Promise<{ order?: string }> }) {
  const sp = await searchParams;
  const [orders, leads, watch] = await Promise.all([
    engineOr<any[]>("/orders", []), engineOr<any[]>(`/leads${sp.order ? `?order_id=${sp.order}` : ""}`, []),
    engineOr<any[]>("/hot-leads?list=watch&limit=200", [])]);
  return (
    <>
      <PageHeader title="Leads sent" />
      <ErrorNote error={leads.error} />
      <LeadsTable orders={orders.data} leads={leads.data} orderId={sp.order ? Number(sp.order) : undefined} />
      <WatchList leads={watch.data} />
    </>
  );
}
