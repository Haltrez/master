"use client";
import { useState } from "react";
import { Badge, Button, Card } from "@/components/ui";
import { api } from "@/lib/api";

export function GoogleCard({ google, assistant, flash }: { google: any; assistant: any; flash?: string }) {
  const [g, setG] = useState(google);
  return (
    <Card title="Google Sheets & assistant">
      {flash === "ok" && <p className="mb-2 text-sm text-emerald-700 dark:text-emerald-400">Google connected ✓</p>}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">Google: {g.connected
          ? <><Badge tone="green">connected</Badge> <span className="text-slate-500">{g.email}</span></>
          : <Badge>not connected</Badge>}</p>
        {g.client_set && (g.connected
          ? <Button size="sm" variant="ghost" onClick={async () => { await api("/google/disconnect", { method: "POST" }); setG({ ...g, connected: false, email: null }); }}>Disconnect</Button>
          : <a href="/api/google/start" className="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white no-underline hover:no-underline">Connect Google</a>)}
      </div>
      {!g.client_set && (
        <p className="mt-2 text-xs text-slate-500">Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in the server's .env first (see README, "Google Sheets").</p>
      )}
      <p className="mt-2 text-xs text-slate-500">Each order gets its own sheet in your Google Drive, shared with the agency's email.
        The app can only see sheets it made itself.</p>
      <p className="mt-4 text-sm">Assistant: {assistant?.key_set ? <Badge tone="green">on</Badge> : <Badge>no ANTHROPIC_API_KEY</Badge>}</p>
    </Card>
  );
}
