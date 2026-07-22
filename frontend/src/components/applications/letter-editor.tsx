import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  useRewriteParagraph,
  type RunSummary,
} from "@/lib/queries/applications";
import { useFullList, type ResumeSnippetRow } from "@/lib/queries/jac";
import { seedDiscussion, type ChatMessage } from "@/lib/letter-chat";
import {
  appendParagraph,
  hasStub,
  replaceRange,
  replaceStub,
  type LetterMeta,
} from "@/lib/letter-doc";
import { RefineChat } from "./refine-chat";
import { caretOffset, RewritePopover } from "./rewrite-popover";

function MetaField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <Input value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

const RECIPIENT_FIELDS: [string, string][] = [
  ["company", "Company"],
  ["contact_name", "Contact"],
  ["street", "Street"],
  ["zip", "ZIP"],
  ["city", "City"],
  ["country", "Country"],
  ["email", "Email"],
];

const SENDER_FIELDS: [string, string][] = [
  ["name", "Name"],
  ["street", "Street"],
  ["zip", "ZIP"],
  ["city", "City"],
  ["email", "Email"],
  ["phone", "Phone"],
];

export function LetterEditor({
  applicationId,
  meta,
  onMeta,
  body,
  onBody,
  runs,
}: {
  applicationId: number;
  meta: LetterMeta;
  onMeta: (m: LetterMeta) => void;
  body: string;
  onBody: (b: string) => void;
  runs: RunSummary[];
}) {
  const snippets = useFullList<ResumeSnippetRow>("snippets");
  const rewrite = useRewriteParagraph();
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const [stubDraft, setStubDraft] = useState("");
  const [snippetId, setSnippetId] = useState("");

  // Selection popover (Apple-Pages-style writing tools): tracked from the textarea's
  // own selection events; the range is state, not read at click time, so popover
  // clicks (which blur the textarea) still operate on the highlighted passage.
  const [sel, setSel] = useState<{ start: number; end: number } | null>(null);
  const [anchor, setAnchor] = useState<{
    top: number;
    left: number;
    flip: boolean;
  } | null>(null);
  const [chatSeed, setChatSeed] = useState<ChatMessage | null>(null);

  function onBodySelect() {
    const el = bodyRef.current;
    if (!el) return;
    const { selectionStart: start, selectionEnd: end } = el;
    if (start === end || !el.value.slice(start, end).trim()) {
      setSel(null);
      return;
    }
    setSel({ start, end });
    const pos = caretOffset(el, end);
    // Selections in the lower half flip the popover above the caret — the Card's
    // overflow-hidden would otherwise cut it off mid-panel (refine-chat Results).
    setAnchor({ ...pos, flip: pos.top - el.offsetTop > el.clientHeight / 2 });
  }

  // The recipient block auto-opens while empty. Address web-search is gone — the
  // pipeline extracts the address from the posting text now.
  const recipientEmpty = RECIPIENT_FIELDS.every(
    ([field]) => !(meta.recipient[field] ?? "").trim(),
  );

  const setField = (field: keyof LetterMeta) => (v: string) =>
    onMeta({ ...meta, [field]: v });
  const setBlockField =
    (block: "recipient" | "sender", field: string) => (v: string) =>
      onMeta({ ...meta, [block]: { ...meta[block], [field]: v } });

  function onAppendSnippet() {
    const s = snippets.data?.find((r) => String(r.id) === snippetId);
    if (!s) return;
    onBody(appendParagraph(body, s.content));
    setSnippetId("");
  }

  function onRewrite(instruction: string) {
    if (!sel) return;
    const { start, end } = sel;
    rewrite.mutate(
      {
        id: applicationId,
        text: body.slice(start, end),
        instruction,
      },
      {
        onSuccess: (r) => {
          onBody(replaceRange(body, start, end, r.text));
          setSel(null);
        },
        onError: () =>
          toast.error("Rewrite failed — is the model server running?"),
      },
    );
  }

  function onDiscuss() {
    if (!sel) return;
    setChatSeed(seedDiscussion(body.slice(sel.start, sel.end)));
    setSel(null);
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <div className="col-span-2">
          <MetaField
            label="Subject"
            value={meta.subject}
            onChange={setField("subject")}
          />
        </div>
        <MetaField label="Date" value={meta.date} onChange={setField("date")} />
        <MetaField
          label="Language"
          value={meta.language}
          onChange={setField("language")}
        />
        <div className="col-span-2">
          <MetaField
            label="Salutation"
            value={meta.salutation}
            onChange={setField("salutation")}
          />
        </div>
        <div className="col-span-2">
          <MetaField
            label="Closing"
            value={meta.closing}
            onChange={setField("closing")}
          />
        </div>
      </div>

      <details className="rounded border p-3" open={recipientEmpty}>
        <summary className="cursor-pointer text-sm font-medium">
          Recipient
        </summary>
        <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-3">
          {RECIPIENT_FIELDS.map(([field, label]) => (
            <MetaField
              key={field}
              label={label}
              value={meta.recipient[field] ?? ""}
              onChange={setBlockField("recipient", field)}
            />
          ))}
        </div>
      </details>

      <details className="rounded border p-3">
        <summary className="cursor-pointer text-sm font-medium">Sender</summary>
        <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-3">
          {SENDER_FIELDS.map(([field, label]) => (
            <MetaField
              key={field}
              label={label}
              value={meta.sender[field] ?? ""}
              onChange={setBlockField("sender", field)}
            />
          ))}
        </div>
      </details>

      <div className="relative space-y-1">
        <Label>
          Cover letter body
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            select text for AI rewrite styles
          </span>
        </Label>
        <Textarea
          ref={bodyRef}
          rows={12}
          value={body}
          onChange={(e) => onBody(e.target.value)}
          onSelect={onBodySelect}
          onKeyDown={(e) => {
            if (e.key === "Escape") setSel(null);
          }}
          placeholder="The applied run's letter body lands here — or write your own."
        />
        {sel && anchor && (
          <RewritePopover
            anchor={anchor}
            pending={rewrite.isPending}
            onRewrite={onRewrite}
            onDiscuss={onDiscuss}
            onClose={() => setSel(null)}
          />
        )}
      </div>

      {hasStub(body) && (
        <div className="space-y-2 rounded border border-destructive/50 bg-destructive/10 p-3">
          <p className="text-xs font-medium">
            The body still contains the personal-paragraph stub — not sendable.
            Write your own paragraph to replace it:
          </p>
          <Textarea
            rows={4}
            value={stubDraft}
            onChange={(e) => setStubDraft(e.target.value)}
            placeholder="Why this company, in your own words…"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={!stubDraft.trim()}
              onClick={() => {
                onBody(replaceStub(body, stubDraft));
                setStubDraft("");
              }}
            >
              Replace stub
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onBody(replaceStub(body, ""))}
            >
              Remove stub
            </Button>
          </div>
        </div>
      )}

      <div className="flex items-end gap-2">
        <div className="space-y-1">
          <Label className="text-xs">Append a snippet</Label>
          <Select value={snippetId} onValueChange={setSnippetId}>
            <SelectTrigger className="w-64">
              <SelectValue placeholder="Pick a snippet…" />
            </SelectTrigger>
            <SelectContent>
              {(snippets.data ?? []).map((s) => (
                <SelectItem key={s.id} value={String(s.id)}>
                  {s.kind}: {s.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={!snippetId}
          onClick={onAppendSnippet}
        >
          Append
        </Button>
      </div>

      <RefineChat
        applicationId={applicationId}
        body={body}
        onBody={onBody}
        runs={runs}
        seed={chatSeed}
        onSeedConsumed={() => setChatSeed(null)}
      />
    </div>
  );
}
