"use client";
import { useEffect, useRef, useState } from "react";
import { Button, Card, cn } from "@/components/ui";
import { api, waitFor } from "@/lib/api";

type Msg = { role: "user" | "assistant"; text: string };
const KEY = "rr_assistant_conversation";

export function Assistant({ enabled }: { enabled: boolean }) {
  const [cid, setCid] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let saved: string | null = null;
    try { saved = sessionStorage.getItem(KEY); } catch {}
    if (!saved) return;
    setCid(saved);
    api<any>(`/assistant/conversations/${saved}`).then((r) => setMsgs(r.messages)).catch(() => {});
  }, []);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [msgs, busy]);

  async function send(e?: React.FormEvent) {
    e?.preventDefault();
    const message = text.trim();
    if (!message || busy) return;
    setText("");
    setMsgs((m) => [...m, { role: "user", text: message }]);
    setBusy(true);
    try {
      const start = await api<any>("/assistant/start", { body: { message, conversation_id: cid } });
      setCid(start.conversation_id);
      const t = await waitFor<any>(`/tasks/${start.task_id}`, (x) => x.status !== "running", 2000, 10 * 60_000);
      if (t.status !== "ok") throw new Error(t.error || "the assistant didn't finish");
      const r = t.result;
      setCid(r.conversation_id);
      try { sessionStorage.setItem(KEY, r.conversation_id); } catch {}
      setMsgs((m) => [...m, { role: "assistant", text: r.reply }]);
      if (r.actions?.length) window.dispatchEvent(new Event("orders-changed"));
    } catch (err: any) {
      setMsgs((m) => [...m, { role: "assistant", text: `Error: ${err.message}` }]);
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setCid(null); setMsgs([]);
    try { sessionStorage.removeItem(KEY); } catch {}
  }

  return (
    <Card title="Ask the assistant" action={msgs.length > 0 && <button onClick={reset} className="text-sm text-slate-500">New chat</button>}>
      {!enabled ? (
        <p className="text-sm text-slate-500">Add <code>ANTHROPIC_API_KEY</code> to the server's .env to switch this on.</p>
      ) : (
        <>
          {msgs.length > 0 && (
            <div className="mb-3 max-h-80 space-y-2 overflow-y-auto" data-testid="assistant-log">
              {msgs.map((m, i) => (
                <div key={i} className={cn("max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm",
                  m.role === "user" ? "ml-auto bg-brand text-white" : "bg-slate-100 dark:bg-slate-800")}>{m.text}</div>
              ))}
              {busy && <div className="w-fit rounded-2xl bg-slate-100 px-3 py-2 text-sm text-slate-500 dark:bg-slate-800">Working on it…</div>}
              <div ref={end} />
            </div>
          )}
          <form onSubmit={send} className="flex gap-2">
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} aria-label="Message to the assistant"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) send(e); }}
              placeholder={'e.g. "25 IT leads in Manchester for Apex Recruitment, share with jo@apex.co.uk" or "how is the Acme order doing?"'}
              className="min-h-[44px] flex-1 resize-y rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand focus:ring-2 focus:ring-brand/20 dark:border-slate-700 dark:bg-slate-950" />
            <Button disabled={busy || !text.trim()} className="self-end">Send</Button>
          </form>
        </>
      )}
    </Card>
  );
}
