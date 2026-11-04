"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import { ACTIVITY_LABEL } from "@/lib/format";

// One-click activity logging; every outreach action counts toward the daily 100 at once.
export function QuickLog({ agencyId, contactId, types = ["email", "call", "linkedin_dm", "linkedin_connect"], onLogged }:
  { agencyId: number; contactId?: number | null; types?: string[]; onLogged?: () => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  async function log(type: string) {
    setBusy(type);
    try {
      await api("/activities", { body: { agency_id: agencyId, type, contact_id: contactId || undefined } });
      setDone(type);
      onLogged?.();
      router.refresh();
      setTimeout(() => setDone(null), 1500);
    } finally { setBusy(null); }
  }
  return (
    <div className="flex flex-wrap gap-1">
      {types.map((t) => (
        <Button key={t} size="sm" variant="secondary" disabled={!!busy} onClick={() => log(t)} data-testid={`log-${t}`}>
          {done === t ? "✓ logged" : ACTIVITY_LABEL[t]}
        </Button>
      ))}
    </div>
  );
}
