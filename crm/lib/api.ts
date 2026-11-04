"use client";
// Browser-side calls go through /api/engine/* (session-checked, secret added server-side).
export class ApiError extends Error {
  constructor(public status: number, public detail: any) {
    super(typeof detail === "string" ? detail : detail?.message || detail?.detail || `error ${status}`);
  }
}

export async function api<T = any>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const r = await fetch("/api/engine" + path, {
    method: init.method || (init.body !== undefined ? "POST" : "GET"),
    headers: init.body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (r.status === 401) {
    window.location.href = "/login";
    throw new ApiError(401, "not logged in");
  }
  const text = await r.text();
  let data: any = text;
  try { data = text ? JSON.parse(text) : undefined; } catch {}
  if (!r.ok) throw new ApiError(r.status, data?.detail === "needs_activity" ? data : (data?.detail ?? data));
  return data as T;
}

export async function waitFor<T = any>(path: string, done: (x: T) => boolean, everyMs = 1500, maxMs = 10 * 60_000): Promise<T> {
  const start = Date.now();
  for (;;) {
    const x = await api<T>(path);
    if (done(x) || Date.now() - start > maxMs) return x;
    await new Promise((r) => setTimeout(r, everyMs));
  }
}
