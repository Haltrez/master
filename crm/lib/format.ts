export function d(v?: string | null): string {
  if (!v) return "–";
  const x = new Date(v.length === 10 ? v + "T00:00:00" : v);
  return x.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "Europe/London" });
}
export function dt(v?: string | null): string {
  if (!v) return "–";
  return new Date(v).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    timeZone: "Europe/London" });
}
export function ago(v?: string | null): string {
  if (!v) return "never";
  const s = (Date.now() - new Date(v).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
export function gbp(n: number): string {
  return new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", maximumFractionDigits: 0 }).format(n || 0);
}
export const STAGES = ["new", "researching", "contacted", "replied", "call_booked", "pilot", "paid", "lost", "do_not_contact"] as const;
export const STAGE_LABEL: Record<string, string> = {
  new: "New", researching: "Researching", contacted: "Contacted", replied: "Replied", call_booked: "Call booked",
  pilot: "Pilot", paid: "Paid", lost: "Lost", do_not_contact: "Do not contact",
};
export const ACTIVITY_LABEL: Record<string, string> = {
  email: "Email", call: "Call", linkedin_dm: "LinkedIn DM", linkedin_connect: "LinkedIn connect", meeting: "Meeting", note: "Note",
};
