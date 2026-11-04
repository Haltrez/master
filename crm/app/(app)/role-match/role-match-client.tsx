"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Candidates } from "@/components/candidates";
import { Button, Card, Input, Label, Select } from "@/components/ui";
import { api } from "@/lib/api";

export function RoleMatchClient({ agencies, families, bundle }: { agencies: any[]; families: any[]; bundle: any }) {
  const router = useRouter();
  const [name, setName] = useState("");
  async function create(e: React.FormEvent) {
    e.preventDefault();
    const a = await api("/agencies", { body: { name } });
    router.push(`/role-match?agency=${a.id}`);
  }
  return (
    <div className="space-y-4">
      <Card title="Which agency?">
        <div className="grid gap-3 sm:grid-cols-2">
          <div><Label>Existing agency</Label>
            <Select value={bundle?.agency?.id || ""} onChange={(e) => router.push(e.target.value ? `/role-match?agency=${e.target.value}` : "/role-match")}>
              <option value="">Pick an agency…</option>
              {agencies.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select></div>
          <form onSubmit={create}><Label>Or a new one</Label>
            <div className="flex gap-2"><Input required value={name} onChange={(e) => setName(e.target.value)} placeholder="MCM Recruitment Solutions" />
              <Button>Add</Button></div></form>
        </div>
        {bundle && <p className="mt-2 text-sm"><Link href={`/agencies/${bundle.agency.id}`}>Open {bundle.agency.name}</Link></p>}
      </Card>
      {bundle && <Candidates agencyId={bundle.agency.id} briefs={bundle.briefs} matches={bundle.matches} families={families} />}
    </div>
  );
}
