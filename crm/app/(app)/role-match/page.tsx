import { PageHeader } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { RoleMatchClient } from "./role-match-client";

export const dynamic = "force-dynamic";

export default async function RoleMatchPage({ searchParams }: { searchParams: Promise<{ agency?: string }> }) {
  const { agency } = await searchParams;
  const [agencies, families] = await Promise.all([engineOr<any[]>("/agencies", []), engineOr<any[]>("/role-families", [])]);
  const selected = agency ? await engineOr<any>(`/agencies/${Number(agency)}`, null) : { data: null };
  return (
    <>
      <PageHeader title="Role Match" />
      <p className="-mt-3 mb-4 text-sm text-slate-500">An agency has candidates waiting? Enter the roles and towns: Role Match finds direct employers hiring those roles nearby.</p>
      <RoleMatchClient agencies={agencies.data} families={families.data} bundle={selected.data} />
    </>
  );
}
