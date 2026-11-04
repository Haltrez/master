// Signed session cookie, verified in middleware (Edge) and route handlers (Node).
// Value: "<expiry ms>.<base64url HMAC-SHA256(SESSION_SECRET, 'v1.<expiry>')>"
export const SESSION_COOKIE = "rr_session";
export const SESSION_DAYS = 30;

function b64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function hmac(secret: string, data: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]);
  return b64url(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data)));
}

function secret(): string {
  const s = process.env.SESSION_SECRET || "";
  if (s.length < 32) throw new Error("SESSION_SECRET must be at least 32 characters");
  return s;
}

export async function makeSession(now = Date.now()): Promise<{ value: string; expires: Date }> {
  const exp = now + SESSION_DAYS * 86400_000;
  return { value: `${exp}.${await hmac(secret(), `v1.${exp}`)}`, expires: new Date(exp) };
}

function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

export async function verifySession(value: string | undefined, now = Date.now()): Promise<boolean> {
  if (!value) return false;
  const [expStr, sig] = value.split(".");
  const exp = Number(expStr);
  if (!exp || !sig || exp < now) return false;
  try {
    return safeEqual(sig, await hmac(secret(), `v1.${exp}`));
  } catch {
    return false;
  }
}
