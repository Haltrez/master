import { notFound } from "next/navigation";
import { ErrorNote } from "@/components/ui";
import { engine, EngineError } from "@/lib/engine";
import { AgencyClient } from "./agency-client";

export const dynamic = "force-dynamic";

export default async function AgencyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  try {
    const [bundle, templates, families] = await Promise.all([
      engine<any>(`/agencies/${Number(id)}`), engine<any[]>("/templates"), engine<any[]>("/role-families")]);
    return <AgencyClient bundle={bundle} templates={templates} families={families} />;
  } catch (e) {
    if (e instanceof EngineError && e.status === 404) notFound();
    return <ErrorNote error={`Can't load this agency: ${(e as Error).message}`} />;
  }
}
