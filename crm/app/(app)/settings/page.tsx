import Link from "next/link";
import { Badge, Card, ErrorNote, PageHeader } from "@/components/ui";
import { engineBase, engineOr } from "@/lib/engine";
import { SettingsForm } from "./settings-form";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  const [health, s] = await Promise.all([engineOr<any>("/health", null), engineOr<any>("/settings", null)]);
  return (
    <>
      <PageHeader title="Settings"><Link href="/settings/role-families">Role families →</Link></PageHeader>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Engine connection">
          <p className="text-sm">{engineBase()} {health.data?.db ? <Badge tone="green">connected</Badge> : <Badge tone="red">not reachable</Badge>}</p>
          <ErrorNote error={health.error} />
          {s.data && (
            <ul className="mt-3 grid grid-cols-2 gap-1 text-sm">
              {Object.entries(s.data.configured).map(([k, v]) => (
                <li key={k} className="flex items-center gap-2">{v ? <Badge tone="green">on</Badge> : <Badge tone="amber">no key</Badge>} {k.replace("_", " ")}</li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-slate-500">API keys live in the server's .env file, never in the browser.</p>
        </Card>
        {s.data && <SettingsForm settings={s.data.settings} />}
      </div>
    </>
  );
}
