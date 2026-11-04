import { ErrorNote, PageHeader } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { ClientsClient } from "./clients-client";

export const dynamic = "force-dynamic";

export default async function ClientsPage() {
  const [clients, searches] = await Promise.all([engineOr<any[]>("/clients", []), engineOr<any[]>("/searches", [])]);
  return (
    <>
      <PageHeader title="Clients" />
      <ErrorNote error={clients.error} />
      <ClientsClient clients={clients.data} searches={searches.data} />
    </>
  );
}
