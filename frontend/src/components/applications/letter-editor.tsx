import { useEffect, useId, useRef, useState } from "react";
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
  useShortenLetter,
  type RunSummary,
} from "@/lib/queries/applications";

import { seedDiscussion, type ChatMessage } from "@/lib/letter-chat";
import {
  countWords,
  hasStub,
  replaceRange,
  type LetterMeta,
} from "@/lib/letter-doc";
import {
  ADDRESS_FORMS,
  FORM_WORD,
  isGerman,
  registerLeaks,
  type AddressForm,
} from "@/lib/register";
import {
  fitIndex,
  shortenTarget,
  type FitIndex,
} from "@/lib/render/letter-fit";
import type { LayoutSpec } from "@/lib/render/spec";
import { LetterDocument, pdfPages } from "@/lib/render/templates";
import { HighlightedTextarea } from "./highlighted-textarea";
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
  const id = useId();
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-xs">
        {label}
      </Label>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

const MOVED_ON =
  "The letter changed while the AI was working, so its result was dropped.";

// Radix Select forbids an empty-string item value; this sentinel = "no form" (nothing
// is checked, and the AI tools get no pronoun rule).
const NO_FORM = "__none__";

const FORM_LABELS: Record<AddressForm, string> = {
  sie: "Sie — formal",
  ihr: "ihr — the team",
  du: "du — one person",
};

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
  letterPages,
  spec,
  runs,
}: {
  applicationId: number;
  meta: LetterMeta;
  onMeta: (m: LetterMeta) => void;
  body: string;
  onBody: (b: string) => void;
  letterPages: number | null;
  spec: LayoutSpec | undefined;
  runs: RunSummary[];
}) {
  const rewrite = useRewriteParagraph();
  const shorten = useShortenLetter();
  const bodyId = useId();
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const [stubDraft, setStubDraft] = useState("");

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

  // Where page 1 ends. Only measured when the preflight already says it spills — the
  // binary search is ~8 renders, too expensive to run speculatively. A stub body is
  // skipped outright: the preflight measures it stripped, so the indices would not agree,
  // and it is export-blocked regardless. The result is tagged with the inputs it was
  // measured for and DERIVED below, so a stale measurement can never highlight new text
  // (and the effect never sets state synchronously — the react-hooks lint rejects that).
  const measurable =
    spec != null && letterPages != null && letterPages > 1 && !hasStub(body);
  const [measured, setMeasured] = useState<{
    body: string;
    meta: LetterMeta;
    spec: LayoutSpec;
    fit: FitIndex | null;
  } | null>(null);
  const [shortening, setShortening] = useState(false);
  // Both AI jobs splice into the text they started from and can wait minutes for the model
  // to wake, so the body takes no typing in the meantime: it would vanish with the result.
  const locked = shortening || rewrite.isPending;
  const lockLabel = shortening
    ? (shorten.jobLabel ?? "Shortening…")
    : (rewrite.jobLabel ?? "Rewriting…");

  useEffect(() => {
    if (!measurable || !spec) return;
    let cancelled = false;
    fitIndex(body, (text) =>
      pdfPages(LetterDocument({ spec, meta, body: text })),
    )
      .then((fit) => {
        if (!cancelled) setMeasured({ body, meta, spec, fit });
      })
      .catch(() => {}); // a failed render just means no highlight
    return () => {
      cancelled = true;
    };
  }, [measurable, spec, meta, body]);

  const overflow =
    measurable &&
    measured?.body === body &&
    measured.meta === meta &&
    measured.spec === spec
      ? measured.fit
      : null;

  // The lock only keeps the keyboard out. The assistant's "apply" or a run applied from the
  // panel can still replace the body during the wait, and a result built from the older text
  // must not undo them.
  const movedOn = (from: string) => bodyRef.current?.value !== from;

  /**
   * Ask for a shorter letter, re-measure, repeat. Bounded at 3 attempts: a model that
   * can't hit the budget in three tries won't hit it in ten, and each attempt is a paid
   * call. Every attempt starts from the CURRENT text, so the letter converges instead of
   * being rewritten from scratch each time.
   */
  async function onShortenToFit() {
    if (!spec || !overflow) return;
    setShortening(true);
    try {
      let text = body;
      let fit: FitIndex | null = overflow;
      let failed = false;
      for (let attempt = 0; attempt < 3 && fit; attempt++) {
        let res: { body: string };
        try {
          res = await shorten.mutateAsync({
            id: applicationId,
            body: text,
            targetWords: shortenTarget(fit),
            language: meta.language,
            addressForm: meta.address_form,
          });
        } catch {
          // The job fails when the model yields nothing. Caught per attempt, not around the
          // loop, so an earlier successful pass is not thrown away.
          failed = true;
          break;
        }
        text = res.body;
        fit = await fitIndex(text, (t) =>
          pdfPages(LetterDocument({ spec, meta, body: t })),
        );
      }
      if (movedOn(body)) {
        toast.warning(MOVED_ON);
        return;
      }
      if (text !== body) onBody(text);
      if (failed)
        toast.error(
          text === body
            ? "Shortening failed — the letter is unchanged."
            : "Shortening failed part-way — keeping the shorter version so far.",
        );
      else if (fit)
        toast.warning(
          "Still a little long — trim the highlighted part by hand.",
        );
      else toast.success("The letter fits on one page.");
    } finally {
      setShortening(false);
    }
  }

  // The recipient block opens on mount if empty, and from then on it's the user's: tied to
  // the live value it would snap shut on the first letter typed, taking the focus along.
  // Address web-search is gone — the pipeline extracts the address from the posting text now.
  const [recipientOpen, setRecipientOpen] = useState(() =>
    RECIPIENT_FIELDS.every(([field]) => !(meta.recipient[field] ?? "").trim()),
  );

  const setField = (field: keyof LetterMeta) => (v: string) =>
    onMeta({ ...meta, [field]: v });
  const setBlockField =
    (block: "recipient" | "sender", field: string) => (v: string) =>
      onMeta({ ...meta, [block]: { ...meta[block], [field]: v } });

  function onRewrite(instruction: string) {
    if (!sel) return;
    const { start, end } = sel;
    rewrite.mutate(
      {
        id: applicationId,
        text: body.slice(start, end),
        instruction,
        language: meta.language,
        addressForm: meta.address_form,
      },
      {
        onSuccess: (r) => {
          if (movedOn(body)) toast.warning(MOVED_ON);
          else onBody(replaceRange(body, start, end, r.text));
          setSel(null);
        },
        onError: () =>
          toast.error("Rewrite failed — the AI may be unavailable right now."),
      },
    );
  }

  // German letters: pronouns that contradict the letter's form of "you". Re-checked on
  // every render, so hand edits and AI rewrites are covered alike. A flag, never a
  // rewrite — same rule as the server's check of the run's body.
  const leaks = registerLeaks(body, meta.language, meta.address_form);

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
        {isGerman(meta.language) && (
          <div className="col-span-2 space-y-1">
            <Label className="text-xs">Form of address</Label>
            <Select
              value={meta.address_form || NO_FORM}
              onValueChange={(v) =>
                onMeta({ ...meta, address_form: v === NO_FORM ? "" : v })
              }
            >
              <SelectTrigger className="w-full" aria-label="Form of address">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ADDRESS_FORMS.map((f) => (
                  <SelectItem key={f} value={f}>
                    {FORM_LABELS[f]}
                  </SelectItem>
                ))}
                <SelectItem value={NO_FORM}>not set — no check</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
      </div>

      <details
        className="rounded border p-3"
        open={recipientOpen}
        onToggle={(e) => setRecipientOpen(e.currentTarget.open)}
      >
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
        <Label htmlFor={bodyId}>
          Cover letter body
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            select text for AI rewrite styles
          </span>
        </Label>
        {/* The popover renders INSIDE the highlighted textarea: that box is the
            textarea's offsetParent, which is what caretOffset measures against. */}
        <HighlightedTextarea
          id={bodyId}
          textareaRef={bodyRef}
          rows={12}
          value={body}
          onChange={onBody}
          onSelect={onBodySelect}
          onKeyDown={(e) => {
            if (e.key === "Escape") setSel(null);
          }}
          placeholder="The applied run's letter body lands here — or write your own."
          overflowAt={overflow?.index ?? null}
          readOnly={locked}
        >
          {sel && anchor && (
            <RewritePopover
              anchor={anchor}
              pending={locked}
              pendingLabel={lockLabel}
              onRewrite={onRewrite}
              onDiscuss={onDiscuss}
              onClose={() => setSel(null)}
            />
          )}
        </HighlightedTextarea>
        {/* Outside the popover: closing that must not leave a read-only box unexplained. */}
        <p role="status" className="text-xs text-muted-foreground">
          {locked &&
            `${lockLabel} The letter is read-only until the AI answers.`}
        </p>
        {letterPages != null && letterPages > 1 && (
          <div className="flex items-center gap-2">
            <p className="text-xs text-destructive">
              {overflow
                ? `About ${countWords(body) - overflow.words} words too long — the highlighted tail runs onto page ${letterPages}.`
                : `This letter runs to ${letterPages} pages — it should be one.`}
            </p>
            {!hasStub(body) && (
              <Button
                size="sm"
                variant="outline"
                disabled={!overflow || locked}
                onClick={onShortenToFit}
              >
                {shortening
                  ? (shorten.jobLabel ?? "Shortening…")
                  : "Shorten to fit"}
              </Button>
            )}
          </div>
        )}
        {leaks.length > 0 && (
          <p className="text-xs text-amber-700 dark:text-amber-400">
            Wrong form of “you” for a “
            {FORM_WORD[meta.address_form as AddressForm] ?? meta.address_form}”
            letter: {leaks.join(", ")}. Flagged, not rewritten — fix them by
            hand or with a rewrite.
          </p>
        )}
      </div>

      {hasStub(body) && (
        <div className="space-y-2 rounded border border-destructive/50 bg-destructive/10 p-3">
          <p className="text-xs font-medium">
            The body still contains the personal-paragraph stub — not sendable.
            Write your own paragraph to replace it:
          </p>
          <Textarea
            aria-label="Your own paragraph"
            rows={4}
            value={stubDraft}
            onChange={(e) => setStubDraft(e.target.value)}
            placeholder="Why this company, in your own words…"
          />
        </div>
      )}

      <RefineChat
        applicationId={applicationId}
        body={body}
        letter={meta}
        onBody={onBody}
        runs={runs}
        seed={chatSeed}
        onSeedConsumed={() => setChatSeed(null)}
      />
    </div>
  );
}
