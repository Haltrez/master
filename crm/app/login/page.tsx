import { VERSION } from "@/lib/version";
import { login } from "./actions";

export const metadata = { title: "Sign in · Repost Radar" };

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string; error?: string }> }) {
  const { next, error } = await searchParams;
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form action={login} className="w-full max-w-sm space-y-4 rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div>
          <h1 className="text-xl font-semibold">Repost Radar</h1>
          <p className="text-sm text-slate-500">SmartEmploy CRM</p>
        </div>
        <input type="hidden" name="next" value={next || "/"} />
        <label className="block text-sm font-medium" htmlFor="password">Password</label>
        <input id="password" name="password" type="password" required autoFocus autoComplete="current-password"
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-950" />
        {error && <p className="text-sm text-red-600" role="alert">{error === "rate" ? "Too many attempts. Wait 15 minutes." : "Wrong password."}</p>}
        <button className="w-full rounded-lg bg-brand px-4 py-2 font-medium text-white hover:bg-brand-dark">Sign in</button>
        <p className="text-center text-xs text-slate-400" data-version={VERSION}>Version {VERSION}</p>
      </form>
    </main>
  );
}
