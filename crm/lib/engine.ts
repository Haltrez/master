import "server-only";

// Server-side calls to the engine. ENGINE_API_SECRET never reaches the browser.
export class EngineError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : `engine returned ${status}`);
  }
}

export function engineBase(): string {
  return (process.env.ENGINE_URL || "http://localhost:8000").replace(/\/$/, "");
}

export async function engineFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-API-Key", process.env.ENGINE_API_SECRET || "");
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return fetch(engineBase() + path, { ...init, headers, cache: "no-store" });
}

export async function engine<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await engineFetch(path, init);
  if (!r.ok) {
    let detail: unknown = await r.text();
    try { detail = JSON.parse(detail as string).detail ?? detail; } catch {}
    throw new EngineError(r.status, detail);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

export async function engineOr<T>(path: string, fallback: T): Promise<{ data: T; error?: string }> {
  try {
    return { data: await engine<T>(path) };
  } catch (e: any) {
    return { data: fallback, error: e?.message || "engine unreachable" };
  }
}
