import { ErrorNote, PageHeader } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { TemplatesClient } from "./templates-client";

export const dynamic = "force-dynamic";

export default async function TemplatesPage() {
  const [t, a] = await Promise.all([engineOr<any[]>("/templates", []), engineOr<any[]>("/agencies", [])]);
  return (
    <>
      <PageHeader title="Templates" />
      <ErrorNote error={t.error} />
      <TemplatesClient templates={t.data} agencies={a.data.slice(0, 300)} />
    </>
  );
}
