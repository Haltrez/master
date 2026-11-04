import { ErrorNote } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { Assistant } from "./home/assistant";
import { Orders } from "./home/orders";
import { Territories } from "./home/territories";

export const dynamic = "force-dynamic";

export default async function Home({ searchParams }: { searchParams: Promise<{ google?: string; reason?: string }> }) {
  const sp = await searchParams;
  const [orders, industries, settings] = await Promise.all([
    engineOr<any[]>("/orders", []), engineOr<any[]>("/industries", []), engineOr<any>("/settings", null)]);
  return (
    <div className="mx-auto max-w-4xl space-y-5">
      {sp.google === "error" && <ErrorNote error={`Google didn't connect: ${sp.reason || "try again"}`} />}
      <ErrorNote error={orders.error && `Can't reach the engine: ${orders.error}`} />
      <Assistant enabled={!!settings.data?.assistant?.key_set} />
      <Orders initial={orders.data} industries={industries.data} google={settings.data?.google} />
      <Territories industries={industries.data} />
    </div>
  );
}
