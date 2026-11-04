"use server";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { checkPassword, clearFailures, recordFailure, tooManyAttempts } from "@/lib/password";
import { SESSION_COOKIE, makeSession } from "@/lib/session";

export async function login(form: FormData) {
  const h = await headers();
  const ip = (h.get("x-forwarded-for") || "").split(",")[0].trim() || "local";
  const nextPath = String(form.get("next") || "/");
  const safeNext = nextPath.startsWith("/") && !nextPath.startsWith("//") ? nextPath : "/";
  if (tooManyAttempts(ip)) redirect(`/login?error=rate`);
  if (!checkPassword(String(form.get("password") || ""))) {
    recordFailure(ip);
    redirect(`/login?error=1&next=${encodeURIComponent(safeNext)}`);
  }
  clearFailures(ip);
  const s = await makeSession();
  (await cookies()).set(SESSION_COOKIE, s.value, {
    httpOnly: true, sameSite: "lax", secure: process.env.NODE_ENV === "production" && process.env.INSECURE_COOKIES !== "1",
    path: "/", expires: s.expires,
  });
  redirect(safeNext);
}
