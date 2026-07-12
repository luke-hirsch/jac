/**
 * Ephemeral letter-refinement chat ([fullstack]-letter-refine-chat): client-held
 * transcript + one sync completion per turn; gone on reload by design. Offered only
 * when a standard+ alias is configured — a 1B model can't chat usefully. A reply may
 * carry a proposed replacement body (`revision`); applying it routes through `onBody`,
 * so the normal Save flow still gates persistence.
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
import {
  chatAliases,
  chatPayload,
  preferredRefineAlias,
  type ChatMessage,
} from "@/lib/letter-chat";
import { useLetterChat, type RunSummary } from "@/lib/queries/applications";
import { useLLMAliases } from "@/lib/queries/llm";

type Entry = ChatMessage & { revision?: string | null };

const toApi = (entries: Entry[]): ChatMessage[] =>
  entries.map(({ role, content }) => ({ role, content }));

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
  runs: RunSummary[];
  /** A popover hand-off ("discuss this passage") — appended and sent once. */
  seed: ChatMessage | null;
  onSeedConsumed: () => void;
}) {
  const aliases = useLLMAliases();
  const chat = useLetterChat();
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState("");
  const [pickedAlias, setPickedAlias] = useState<string | null>(null);
  const detailsRef = useRef<HTMLDetailsElement>(null);

  const capable = chatAliases(aliases.data ?? []);
  const alias =
    pickedAlias ?? preferredRefineAlias(aliases.data ?? [], runs) ?? "";

  function send(content: string) {
    const text = content.trim();
    if (!text || !alias || chat.isPending) return;
    const next: Entry[] = [...entries, { role: "user", content: text }];
    setEntries(next);
    setInput("");
    chat.mutate(
      { id: applicationId, payload: chatPayload(alias, body, toApi(next)) },
      {
        onSuccess: (r) =>
          setEntries([
            ...next,
            {
              role: "assistant",
              content: r.reply || "Here is a proposed revision:",
              revision: r.revision,
            },
          ]),
        onError: () =>
          toast.error("Chat failed — is the model server running?"),
      },
    );
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

  if (aliases.data && capable.length === 0) return null;

  return (
    <details ref={detailsRef} className="rounded border p-3">
      <summary className="cursor-pointer text-sm font-medium">
        Refine with AI (chat)
        <span className="ml-2 text-xs font-normal text-muted-foreground">
          not saved — gone on reload
        </span>
      </summary>
      <div className="mt-2 space-y-2">
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
              <p className="whitespace-pre-wrap">{m.content}</p>
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
          {chat.isPending && (
            <p className="text-xs text-muted-foreground">Thinking…</p>
          )}
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
            placeholder="Ask about the letter — e.g. 'is the opening too generic?'"
          />
          <div className="space-y-1">
            <Select value={alias} onValueChange={setPickedAlias}>
              <SelectTrigger className="h-8 w-36 text-xs">
                <SelectValue placeholder="Model" />
              </SelectTrigger>
              <SelectContent>
                {capable.map((a) => (
                  <SelectItem key={a.alias} value={a.alias}>
                    {a.alias} ({a.strength})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              size="sm"
              className="w-full"
              disabled={!input.trim() || chat.isPending || !alias}
              onClick={() => send(input)}
            >
              Send
            </Button>
          </div>
        </div>
      </div>
    </details>
  );
}
