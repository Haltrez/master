"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ErrorNote, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { d } from "@/lib/format";

// The agency's own CRM export: companies they already have are left out of their leads, or marked with the owner.
export function CrmList({ agency }: { agency: any }) {
  const router = useRouter();
  const [err, setErr] = useState<string>();
  const [msg, setMsg] = useState<string>();
  async function upload(file?: File) {
    if (!file) return;
    setErr(undefined); setMsg(undefined);
    try {
      const r = await api<any>(`/agencies/${agency.id}/crm-list`, { method: "PUT", body: { csv: await file.text() } });
      setMsg(`${r.companies} companies loaded${r.with_owner ? `, ${r.with_owner} with an owner` : ""}.`);
      router.refresh();
    } catch (e: any) { setErr(e.message); }
  }
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
      <span>CRM list: {agency.crm_companies ? `${agency.crm_companies} companies (uploaded ${d(agency.crm_uploaded_at)})` : "none"}</span>
      <label className="cursor-pointer text-brand">
        {agency.crm_companies ? "Replace" : "Upload CSV"}
        <input type="file" accept=".csv,text/csv" className="hidden" aria-label={`CRM list for ${agency.name}`}
          onChange={(e) => upload(e.target.files?.[0])} />
      </label>
      <Select className="w-auto py-0.5 text-xs" defaultValue={agency.crm_match_mode} aria-label={`CRM matches for ${agency.name}`}
        onChange={async (e) => { await api(`/agencies/${agency.id}/crm-list`, { method: "PATCH", body: { mode: e.target.value } }); router.refresh(); }}>
        <option value="exclude">their companies: leave out</option>
        <option value="flag">their companies: send, mark owner</option>
      </Select>
      {msg && <span className="text-emerald-600">{msg}</span>}
      <ErrorNote error={err} />
    </div>
  );
}
