import Link from "next/link";
import { ErrorNote, PageHeader } from "@/components/ui";
import { engineOr } from "@/lib/engine";
import { OutreachClient } from "./outreach-client";

export const dynamic = "force-dynamic";

// Cold email to agencies through Smartlead. Every Smartlead call goes through the engine; the key never reaches the browser.
export default async function OutreachPage() {
  const { data: s, error } = await engineOr<any>("/outreach/settings", null);
  return (
    <>
      <PageHeader title="Outreach"><Link href="/settings/outreach" className="text-sm">Outreach settings</Link></PageHeader>
      <ErrorNote error={error} />
      {s && !s.key_set && <ErrorNote error="SMARTLEAD_API_KEY isn't set on the server yet." />}
      {s && s.key_set && <OutreachClient settings={s} />}
    </>
  );
}
