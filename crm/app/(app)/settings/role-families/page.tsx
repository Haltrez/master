import { PageHeader } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { FamiliesClient } from "./families-client";

export const dynamic = "force-dynamic";

export default async function FamiliesPage() {
  const { data } = await engineOr<any[]>("/role-families", []);
  return (
    <>
      <PageHeader title="Role families" />
      <p className="-mt-3 mb-4 text-sm text-slate-500">Which job titles count as the same role in Role Match. Exact titles score higher than related ones.</p>
      <FamiliesClient families={data} />
    </>
  );
}
