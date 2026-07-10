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
import { useRewriteParagraph } from "@/lib/queries/applications";
import { useFullList, type ResumeSnippetRow } from "@/lib/queries/jac";
import {
  appendParagraph,
  hasStub,
  replaceRange,
  replaceStub,
  type LetterMeta,
} from "@/lib/letter-doc";

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
}: {
  applicationId: number;
  meta: LetterMeta;
  onMeta: (m: LetterMeta) => void;
  body: string;
  onBody: (b: string) => void;
}) {
  const snippets = useFullList<ResumeSnippetRow>("snippets");
  const rewrite = useRewriteParagraph();
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const [stubDraft, setStubDraft] = useState("");
  const [snippetId, setSnippetId] = useState("");
  const [instruction, setInstruction] = useState("");

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

  function onRewrite() {
    const el = bodyRef.current;
    if (!el) return;
    // The selection indexes into the *draft* string the textarea renders, so the splice
    // below is exact. The selection is read at click time — mutate on the captured range.
    const start = el.selectionStart;
    const end = el.selectionEnd;
    if (!body.slice(start, end).trim()) {
      toast.error("Select the passage to rewrite in the body first");
      return;
    }
    rewrite.mutate(
      { id: applicationId, text: body.slice(start, end), instruction },
      {
        onSuccess: (r) => onBody(replaceRange(body, start, end, r.text)),
        onError: () =>
          toast.error("Rewrite failed — is the model server running?"),
      },
    );
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

      <details className="rounded border p-3">
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

      <div className="space-y-1">
        <Label>Cover letter body</Label>
        <Textarea
          ref={bodyRef}
          rows={12}
          value={body}
          onChange={(e) => onBody(e.target.value)}
          placeholder="The applied run's letter body lands here — or write your own."
        />
      </div>

      <div className="flex items-end gap-2">
        <div className="flex-1 space-y-1">
          <Label className="text-xs">
            AI rewrite (select text in the body first)
          </Label>
          <Input
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="Optional instruction — e.g. shorter, more formal…"
          />
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={onRewrite}
          disabled={rewrite.isPending}
        >
          {rewrite.isPending ? "Rewriting…" : "Rewrite selection"}
        </Button>
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
    </div>
  );
}
