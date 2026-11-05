import { PageHeader } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { OutreachSettings } from "./outreach-settings";

export const dynamic = "force-dynamic";

export default async function OutreachSettingsPage() {
  const [s, c] = await Promise.all([engineOr<any>("/outreach/settings", null), engineOr<any[]>("/outreach/campaigns", [])]);
  return (
    <>
      <PageHeader title="Outreach settings" />
      {s.data && <OutreachSettings initial={s.data} campaigns={c.data} campaignsError={c.error} />}
    </>
  );
}
