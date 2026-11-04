import { NextResponse, type NextRequest } from "next/server";
import { engine } from "@/lib/engine";
import { publicOrigin } from "@/lib/origin";

export async function GET(req: NextRequest) {
  const origin = publicOrigin(req);
  const p = req.nextUrl.searchParams;
  const fail = (reason: string) => {
    const res = NextResponse.redirect(`${origin}/settings?google=error&reason=${encodeURIComponent(reason)}`);
    res.cookies.set("rr_gstate", "", { path: "/api/google", maxAge: 0 });
    return res;
  };
  if (p.get("error")) return fail(p.get("error")!);
  const state = req.cookies.get("rr_gstate")?.value;
  if (!state || state !== p.get("state")) return fail("the sign-in link expired, try again");
  try {
    await engine("/google/exchange", { method: "POST",
      body: JSON.stringify({ code: p.get("code"), redirect_uri: origin + "/api/google/callback" }) });
  } catch (e: any) {
    return fail(e.message);
  }
  const res = NextResponse.redirect(`${origin}/settings?google=ok`);
  res.cookies.set("rr_gstate", "", { path: "/api/google", maxAge: 0 });
  return res;
}
export const dynamic = "force-dynamic";
