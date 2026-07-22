/**
 * Application assistant ([fullstack]-chat-assistant-rework): a streamed, real
 * multi-turn job-hunting assistant scoped to this application — posting, letter,
 * tailored CV, interview prep, career strategy. Client-held transcript; gone on
 * reload by design, nothing persisted server-side. A reply may end with a proposed
 * replacement body (split client-side from the `REVISED BODY:` marker); applying it
 * routes through `onBody`, so the normal Save flow still gates persistence.
 *
 * Transport is SSE over a raw `fetch` (not the `api()` helper, which buffers the
 * whole body) — CSRF discipline still applies, so unsafe methods carry the same
 * `X-CSRFToken` header `api()` sets, via `csrfHeaders()`.
 */
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { csrfHeaders } from "@/lib/api";
import {
  chatPayload,
  parseSseLine,
  splitRevision,
  type ChatMessage,
} from "@/lib/letter-chat";
import {
  defaultExecutorRow,
  executorDisabledReason,
  useExecutors,
} from "@/lib/queries/llm";
import { type RunSummary } from "@/lib/queries/applications";

type Entry = ChatMessage & { revision?: string | null; streaming?: boolean };

const toApi = (entries: Entry[]): ChatMessage[] =>
  entries.map(({ role, content }) => ({ role, content }));

const CHAT_URL = (id: number) => `/api/jac/applications/${id}/chat/`;

export function RefineChat({
  applicationId,
  body,
  onBody,
  runs,
  seed,
  onSeedConsumed,
}: {
  applicationId: number;
  body: string;
  onBody: (b: string) => void;
  /** This application's runs — only used for the "local model is busy" hint. */
  runs: RunSummary[];
  /** A popover hand-off ("discuss this passage") — appended and sent once. */
  seed: ChatMessage | null;
  onSeedConsumed: () => void;
}) {
  const executors = useExecutors();
  const rows = executors.data ?? [];
  const [picked, setPicked] = useState<{ provider: string; model: string } | null>(
    null,
  );
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const detailsRef = useRef<HTMLDetailsElement>(null);

  // Preselect the backend default once rows arrive; never overwrite an explicit pick.
  if (picked === null && rows.length > 0) {
    const def = defaultExecutorRow(rows);
    if (def) setPicked({ provider: def.provider, model: def.models[0]?.id ?? "" });
  }

  const pickedRow = rows.find((r) => r.provider === picked?.provider) ?? null;
  const pickedReason = pickedRow ? executorDisabledReason(pickedRow) : null;
  const busyTower =
    pickedRow?.self_hosted === true &&
    runs.some((r) => r.status === "pending" || r.status === "running");

  async function send(content: string) {
    const text = content.trim();
    if (!text || pending) return;
    const next: Entry[] = [...entries, { role: "user", content: text }];
    setEntries(next);
    setInput("");
    setPending(true);

    let streamed = "";
    const bubbleIndex = next.length;
    const setBubble = (partial: string, done = false) =>
      setEntries((cur) => {
        const copy = cur.slice(0, bubbleIndex);
        return [
          ...copy,
          { role: "assistant", content: partial, streaming: !done },
        ];
      });
    setBubble("", false);

    try {
      const res = await fetch(CHAT_URL(applicationId), {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...csrfHeaders() },
        body: JSON.stringify(
          chatPayload(
            body,
            toApi(next),
            picked?.provider ? picked : null,
          ),
        ),
      });
      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => null);
        const msg =
          (data?.messages?.[0] as string | undefined) ??
          (data?.provider?.[0] as string | undefined) ??
          (res.status === 429
            ? "Too many messages — wait a moment and try again."
            : "Chat failed — is the model server running?");
        toast.error(msg);
        setEntries(next); // drop the empty streaming bubble
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let failed = false;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          const e = parseSseLine(line);
          if (!e) continue;
          if (e.delta) {
            streamed += e.delta;
            setBubble(streamed, false);
          } else if (e.error) {
            toast.error(e.error);
            failed = true;
          } else if (e.done) {
            const { reply, revision } = splitRevision(streamed);
            setEntries((cur) => [
              ...cur.slice(0, bubbleIndex),
              { role: "assistant", content: reply, revision },
            ]);
          }
        }
      }
      if (failed) setEntries(next); // terminal error — drop the partial bubble
    } catch {
      toast.error("Chat failed — is the model server running?");
      setEntries(next);
    } finally {
      setPending(false);
    }
  }

  // The popover's "Discuss in chat": open the panel, append the seeded message, send.
  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    if (!seed) return;
    if (detailsRef.current) detailsRef.current.open = true;
    sendRef.current(seed.content);
    onSeedConsumed();
  }, [seed, onSeedConsumed]);

  return (
    <details ref={detailsRef} className="rounded border p-3">
      <summary className="cursor-pointer text-sm font-medium">
        Application assistant
        <span className="ml-2 text-xs font-normal text-muted-foreground">
          job-hunting help, letter &amp; CV edits — not saved, gone on reload
        </span>
      </summary>
      <div className="mt-2 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={picked?.provider ?? ""}
            onValueChange={(provider) => {
              const row = rows.find((r) => r.provider === provider);
              if (row)
                setPicked({ provider: row.provider, model: row.models[0]?.id ?? "" });
            }}
          >
            <SelectTrigger className="h-8 w-56 text-xs">
              <SelectValue placeholder="Pick an AI" />
            </SelectTrigger>
            <SelectContent>
              {rows.map((r) => {
                const reason = executorDisabledReason(r);
                return (
                  <SelectItem key={r.provider} value={r.provider} disabled={reason !== null}>
                    {r.label}
                    {r.default ? " · default" : ""}
                    {reason ? ` · ${reason}` : ""}
                  </SelectItem>
                );
              })}
            </SelectContent>
          </Select>
          {pickedRow && !pickedRow.self_hosted && pickedRow.models.length > 1 && (
            <Select
              value={picked?.model ?? ""}
              onValueChange={(model) =>
                setPicked((p) => (p ? { ...p, model } : p))
              }
            >
              <SelectTrigger className="h-8 w-48 text-xs">
                <SelectValue placeholder="Model" />
              </SelectTrigger>
              <SelectContent>
                {pickedRow.models.map((m) => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        {busyTower && (
          <p className="text-xs text-amber-700">
            The local model is busy generating — replies may wait.
          </p>
        )}

        <div className="space-y-2">
          {entries.map((m, i) => (
            <div
              key={i}
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                m.role === "user"
                  ? "ml-auto bg-primary text-primary-foreground"
                  : "bg-muted"
              }`}
            >
              <p className="whitespace-pre-wrap">
                {m.content}
                {m.streaming && "…"}
              </p>
              {m.revision && (
                <div className="mt-2 space-y-1 rounded border bg-background p-2">
                  <p className="whitespace-pre-wrap text-xs text-muted-foreground">
                    {m.revision}
                  </p>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      onBody(m.revision!);
                      toast.success(
                        "Revised body loaded into the editor — Save to keep it.",
                      );
                    }}
                  >
                    Apply revised body
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="flex items-end gap-2">
          <Textarea
            rows={2}
            className="text-sm"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            placeholder="Ask about the posting, the letter, the CV, interview prep…"
          />
          <Button
            size="sm"
            disabled={!input.trim() || pending || pickedReason != null}
            onClick={() => send(input)}
          >
            Send
          </Button>
        </div>
      </div>
    </details>
  );
}
