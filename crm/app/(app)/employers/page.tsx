import { ErrorNote, PageHeader } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { EmployersClient } from "./employers-client";

export const dynamic = "force-dynamic";

export default async function EmployersPage({ searchParams }: { searchParams: Promise<{ q?: string }> }) {
  const { q } = await searchParams;
  const { data, error } = await engineOr<any[]>("/employers", []);
  return (
    <>
      <PageHeader title="Employers" />
      <p className="-mt-3 mb-4 text-sm text-slate-500">Direct employers Role Match watches. Research-queue rows need a careers page link before they can be checked.</p>
      <ErrorNote error={error} />
      <EmployersClient employers={data} initialQ={q || ""} />
    </>
  );
}
