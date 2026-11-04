import { ErrorNote, PageHeader } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { SearchesClient } from "./searches-client";

export const dynamic = "force-dynamic";

export default async function SearchesPage() {
  const { data, error } = await engineOr<any[]>("/searches", []);
  return (
    <>
      <PageHeader title="Searches" />
      <p className="-mt-3 mb-4 text-sm text-slate-500">Each search is a niche + region: a territory you can sell to one client only.</p>
      <ErrorNote error={error} />
      <SearchesClient searches={data} />
    </>
  );
}
