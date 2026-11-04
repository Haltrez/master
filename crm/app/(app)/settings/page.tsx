import Link from "next/link";
import { Badge, Card, ErrorNote, PageHeader } from "@/components/ui";
import { engineBase, engineOr } from "@/lib/engine";
import { ago } from "@/lib/format";
import { GoogleCard } from "./google-card";
import { SettingsForm } from "./settings-form";

export const dynamic = "force-dynamic";

export default async function SettingsPage({ searchParams }: { searchParams: Promise<{ google?: string; reason?: string }> }) {
  const sp = await searchParams;
  const [health, s] = await Promise.all([engineOr<any>("/health", null), engineOr<any>("/settings", null)]);
  return (
    <>
      <PageHeader title="Settings"><Link href="/settings/role-families">Role families →</Link></PageHeader>
      {sp.google === "error" && <div className="mb-4"><ErrorNote error={`Google didn't connect: ${sp.reason || "try again"}`} /></div>}
      <div className="grid gap-4 lg:grid-cols-2">
        {s.data && <GoogleCard google={s.data.google} assistant={s.data.assistant} flash={sp.google} />}
        <Card title="Engine connection">
          <p className="text-sm">{engineBase()} {health.data?.db ? <Badge tone="green">connected</Badge> : <Badge tone="red">not reachable</Badge>}</p>
          <ErrorNote error={health.error} />
          {s.data && (
            <table className="mt-3 w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                <tr><th className="py-1">Source</th><th>Key</th><th>Last run</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {s.data.sources.map((x: any) => (
                  <tr key={x.name}>
                    <td className="py-1.5 font-medium">{x.name}</td>
                    <td>{x.key_set ? <Badge tone="blue">key set</Badge> : <Badge>no key</Badge>}</td>
                    <td className="text-xs">
                      {!x.key_set ? <span className="text-slate-400">–</span>
                        : !x.last ? <span className="text-slate-500">not run yet</span>
                        : x.last.ok ? <span className="text-emerald-700 dark:text-emerald-400">✓ {x.last.ads} ads · {ago(x.last.at)}</span>
                        : <span className="text-red-600" title={x.last.error}>✗ {String(x.last.error).slice(0, 80)} · {ago(x.last.at)}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="mt-2 text-xs text-slate-500">"Key set" only means a key is in .env. The last-run column shows whether it really works.
            Companies House, Brave and Hunter show results on hot leads (verified badges, contacts).</p>
          <p className="mt-3 text-xs text-slate-500">API keys live in the server's .env file, never in the browser.</p>
        </Card>
        {s.data && <SettingsForm settings={s.data.settings} />}
      </div>
    </>
  );
}
