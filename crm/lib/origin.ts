import "server-only";
import type { NextRequest } from "next/server";

// The address the browser used (Caddy passes the host; it terminates HTTPS).
export function publicOrigin(req: NextRequest): string {
  // PUBLIC_URL is https://${DOMAIN}; DOMAIN may list several names ("a.com, b.com"): the first one is used.
  const pub = (process.env.PUBLIC_URL || "").split(",")[0].trim().replace(/\/$/, "");
  if (pub && pub !== "https://") return pub;
  const host = req.headers.get("x-forwarded-host") || req.headers.get("host") || req.nextUrl.host;
  const proto = req.headers.get("x-forwarded-proto") || req.nextUrl.protocol.replace(":", "");
  return `${proto}://${host}`;
}
