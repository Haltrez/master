import { NextResponse, type NextRequest } from "next/server";
import { engineFetch } from "@/lib/engine";

// Browser -> CRM -> engine. Middleware has already checked the session cookie.
async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (req.method !== "GET") {
    const origin = req.headers.get("origin");
    if (origin && new URL(origin).host !== req.headers.get("host")) {
      return NextResponse.json({ detail: "cross-site request refused" }, { status: 403 });
    }
  }
  const target = "/" + path.map(encodeURIComponent).join("/") + (req.nextUrl.search || "");
  const body = ["GET", "HEAD"].includes(req.method) ? undefined : await req.text();
  const r = await engineFetch(target, { method: req.method, body: body || undefined,
    headers: body ? { "Content-Type": req.headers.get("content-type") || "application/json" } : {} });
  const headers = new Headers();
  for (const h of ["content-type", "content-disposition", "x-skipped", "x-skipped-total", "x-exported"]) {
    const v = r.headers.get(h);
    if (v) headers.set(h, v);
  }
  return new NextResponse(r.status === 204 ? null : await r.arrayBuffer(), { status: r.status, headers });
}

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const PUT = forward;
export const DELETE = forward;
export const dynamic = "force-dynamic";
