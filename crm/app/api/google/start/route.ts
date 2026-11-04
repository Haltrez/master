import { NextResponse, type NextRequest } from "next/server";
import { engine } from "@/lib/engine";
import { publicOrigin } from "@/lib/origin";

// "Connect Google": send the owner to Google's consent screen (drive.file: only sheets this app creates).
export async function GET(req: NextRequest) {
  const origin = publicOrigin(req);
  const state = crypto.randomUUID();
  try {
    const { url } = await engine<{ url: string }>(
      `/google/auth-url?redirect_uri=${encodeURIComponent(origin + "/api/google/callback")}&state=${state}`);
    const res = NextResponse.redirect(url);
    res.cookies.set("rr_gstate", state, { httpOnly: true, sameSite: "lax", path: "/api/google", maxAge: 600,
      secure: origin.startsWith("https://") });
    return res;
  } catch (e: any) {
    return NextResponse.redirect(`${origin}/settings?google=error&reason=${encodeURIComponent(e.message)}`);
  }
}
export const dynamic = "force-dynamic";
