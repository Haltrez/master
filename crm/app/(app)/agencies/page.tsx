import { ErrorNote, PageHeader } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { AgenciesClient } from "./agencies-client";

export const dynamic = "force-dynamic";

export default async function Agencies({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const qs = sp.search_id ? `?search_id=${encodeURIComponent(sp.search_id)}` : "";
  const [agencies, searches] = await Promise.all([engineOr<any[]>(`/agencies${qs}`, []), engineOr<any[]>("/searches", [])]);
  return (
    <>
      <PageHeader title="Agencies" />
      <ErrorNote error={agencies.error} />
      <AgenciesClient agencies={agencies.data} searches={searches.data} searchId={sp.search_id || ""} />
    </>
  );
}
