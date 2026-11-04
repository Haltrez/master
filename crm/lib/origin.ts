import "server-only";
import type { NextRequest } from "next/server";

// The address the browser used (Caddy passes the host; it terminates HTTPS).
export function publicOrigin(req: NextRequest): string {
  if (process.env.PUBLIC_URL) return process.env.PUBLIC_URL.replace(/\/$/, "");
  const host = req.headers.get("x-forwarded-host") || req.headers.get("host") || req.nextUrl.host;
  const proto = req.headers.get("x-forwarded-proto") || req.nextUrl.protocol.replace(":", "");
  return `${proto}://${host}`;
}
