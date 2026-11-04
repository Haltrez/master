import "server-only";
import { createHash, scryptSync, timingSafeEqual } from "node:crypto";

// CRM_PASSWORD_HASH = "scrypt:<salt hex>:<key hex>" (make one with: npm run hash-password), or CRM_PASSWORD (plain).
export function checkPassword(input: string): boolean {
  const hash = process.env.CRM_PASSWORD_HASH;
  if (hash) {
    const [kind, salt, key] = hash.split(":");
    if (kind !== "scrypt" || !salt || !key) return false;
    const got = scryptSync(input, Buffer.from(salt, "hex"), key.length / 2);
    return timingSafeEqual(got, Buffer.from(key, "hex"));
  }
  const plain = process.env.CRM_PASSWORD;
  if (!plain) return false;
  const a = createHash("sha256").update(input).digest();
  const b = createHash("sha256").update(plain).digest();
  return timingSafeEqual(a, b);
}

// Very small in-memory brute-force brake: 10 failed attempts per IP per 15 minutes.
const attempts = new Map<string, { n: number; until: number }>();
export function tooManyAttempts(ip: string): boolean {
  const a = attempts.get(ip);
  return !!a && a.until > Date.now() && a.n >= 10;
}
export function recordFailure(ip: string) {
  const now = Date.now();
  const a = attempts.get(ip);
  if (!a || a.until < now) attempts.set(ip, { n: 1, until: now + 15 * 60_000 });
  else a.n += 1;
}
export function clearFailures(ip: string) {
  attempts.delete(ip);
}
