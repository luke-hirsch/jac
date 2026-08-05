# [fullstack] Letter fit — one page, on purpose

> Roadmap: **cover-letter phase, items 1 + 2** — "sometimes the text is too long … the paragraph
> rewriter with 'shorten' is always too effective. after shortening only one paragraph remains" and
> "the date in the cover letter in the top right looks like shit. use the language setting to format
> it."
> Branch: `fullstack/letter-fit`
> Depends on: `[frontend]-fit-preflight` (the measurement harness and the letter page count).

## Context / goal

**The letter is specified to overflow.** `CoverLetterWriter._TARGET_WORDS = (200, 320)`
(`llm_prompts.py:379`). The DIN layout gives the body `297 − 85 − 25 = 187 mm` on page 1
(`templates.tsx:272–275`); at 11pt with `lineHeight: 1.4` that's ~34 lines, minus subject,
salutation, closing, signature block and their margins (~8 lines) ≈ 26 lines of prose ≈ **~230
words**. The top of the target band is 40% over the page. Every downstream fix is a workaround
until this number changes.

**"Shorter" carries no budget.** `ParagraphRewrite._INSTRUCTION` says *"Keep roughly the same length
unless the request says otherwise"* (`llm_prompts.py:636–641`) and the UI sends the bare word
"shorter". The model gets no target and no structural constraint, so it collapses three paragraphs
into one. That's the reported behaviour, and it's a prompt problem, not a model problem.

**Three layout bugs in the same file:**

- the recipient address block and the date are both `fixed` (`templates.tsx:358, 366`), so a letter
  that *does* spill to page 2 prints the recipient's address and the date **again** on it;
- `spec.cover_letter.din5008` is parsed (`spec.ts:89`) and never read — the 85 mm window-envelope
  offset applies to every letter, including the ones that only ever go out as an email attachment,
  donating ~25 mm of page 1;
- the date renders as the raw ISO string (`letter-doc.ts:25`, `templates.tsx:351`).

Outcome: a letter that targets the page it's printed on, a shorten action with an actual word
budget that keeps the paragraph structure, and the overflow shown in the editor — highlighted in
the textarea, with a "shorten to fit" button that loops until it fits or gives up.

## Affected files

| path | why |
| --- | --- |
| `backend/jac/llm_prompts.py` | `_TARGET_WORDS` fixed + overridable; new `ShortenLetter`. |
| `backend/jac/views.py` | `POST …/applications/<pk>/shorten/`. |
| `backend/jac/serializers.py` | the shorten request serializer. |
| `frontend/src/lib/letter-doc.ts` | `formatLetterDate`, `countWords`. |
| `frontend/src/lib/render/letter-fit.ts` | **new** — `fitIndex` (binary search on words), `shortenTarget`. |
| `frontend/src/lib/render/templates.tsx` | `din5008`, the `fixed` repeat, the formatted date. |
| `frontend/src/lib/queries/applications.ts` | `useShortenLetter`. |
| `frontend/src/components/applications/highlighted-textarea.tsx` | **new** — overflow highlight. |
| `frontend/src/components/applications/letter-editor.tsx` | wire both in. |

## The code

### 1. `backend/jac/llm_prompts.py` — the target (line 379)

```python
    # A DIN 5008 page 1 gives the body ~187mm; at 11pt/1.4 that is ~34 lines, and the
    # subject + salutation + closing + signature furniture eats ~8 of them. ~26 lines of
    # prose is ~230 words. The old (200, 320) band was specified to overflow — every
    # "the letter is too long" report starts here, not at the model.
    _TARGET_WORDS = (170, 230)
```

and let a caller override it (the constructor, line 393):

```python
        target_words: tuple[int, int] | None = None,
```
```python
        self.target_words = target_words or self._TARGET_WORDS
```

with `_prompt()` (line 461) reading `lo, hi = self.target_words`.

### 2. `backend/jac/llm_prompts.py` — `ShortenLetter` (new, after `ParagraphRewrite`)

```python
class ShortenLetter:
    """Shorten a whole cover-letter body to a word budget, structure intact.

    Deliberately NOT ParagraphRewrite with "shorter": that instruction gives the model no
    number and no structural constraint, so it happily returns one paragraph where there
    were three. Here the budget is explicit, the paragraph count is a hard rule, and the
    facts are the passage's own (same fabrication rule as everywhere else — no posting).

    Free prose out; any failure -> '' so the caller keeps the original text.
    """

    _INSTRUCTION = (
        "Shorten the cover-letter body below to about {target} words (it is currently "
        "{current}). Rules:\n"
        "  - keep EXACTLY {paragraphs} paragraphs, in the same order and on the same "
        "topics — do not merge, drop, or reorder them;\n"
        "  - keep every factual claim; do not add skills, employers, titles, numbers, "
        "dates or achievements the text does not already state;\n"
        "  - cut adjectives, filler, repetition and throat-clearing, not content;\n"
        "  - keep the voice and the register of the original.\n"
        "Write in {language}. Output ONLY the shortened body — no quotes, no markdown, no "
        "commentary, paragraphs separated by a blank line."
    )
    _MAX_CHARS = 8000  # a letter body, not a document — the view 400s above this

    def __init__(self, body: str, executor, target_words: int, language: str = "en"):
        self.body = body
        self.executor = executor
        self.target_words = max(60, int(target_words))
        self.language = language

    @property
    def paragraphs(self) -> list[str]:
        return [p for p in re.split(r"\n\s*\n", self.body.strip()) if p.strip()]

    def shorten(self) -> str:
        if not self.body.strip():
            return ""
        try:
            raw = complete(prompt=self._prompt(), executor=self.executor)
        except Exception:
            logger.exception("ShortenLetter: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        return (
            self._INSTRUCTION.format(
                target=self.target_words,
                current=len(self.body.split()),
                paragraphs=len(self.paragraphs),
                language=_language_name(self.language),
            )
            + f"\n\nBODY:\n{self.body}\n\nSHORTENED BODY:"
        )
```

### 3. `backend/jac/views.py` + `serializers.py` — the endpoint

Mirror the existing `/rewrite/` action on `JobApplicationViewSet` exactly (same executor
resolution, same throttle, same ownership scoping) — read it before typing this:

```python
    @action(detail=True, methods=["post"], url_path="shorten")
    def shorten(self, request, pk=None):
        """Shorten the whole letter body to a word budget the CLIENT measured. The page fit
        lives in the browser (react-pdf); the server only does the language part."""
        application = self.get_object()
        serializer = ShortenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        executor = self._executor_for(application)  # same helper /rewrite/ uses
        text = ShortenLetter(
            body=data["body"],
            executor=executor,
            target_words=data["target_words"],
            language=data.get("language") or "en",
        ).shorten()
        return Response({"body": text})
```

```python
class ShortenRequestSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=ShortenLetter._MAX_CHARS)
    target_words = serializers.IntegerField(min_value=60, max_value=1000)
    language = serializers.CharField(max_length=8, required=False, allow_blank=True)
```

### 4. `frontend/src/lib/letter-doc.ts`

```ts
/** Month names for the letter date. Only the two languages the letter furniture supports
 *  (cover_letter.py _SALUTATION / _CLOSING); anything else falls back to ISO, which is
 *  unambiguous everywhere and never the US month-first form. */
const MONTHS: Record<string, string[]> = {
  de: ["Januar", "Februar", "März", "April", "Mai", "Juni",
       "Juli", "August", "September", "Oktober", "November", "Dezember"],
  en: ["January", "February", "March", "April", "May", "June",
       "July", "August", "September", "October", "November", "December"],
};

/** ISO date → the long business-letter form: "27. Juli 2026" / "27 July 2026". */
export function formatLetterDate(iso: string, language: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((iso ?? "").trim());
  if (!m) return iso ?? "";
  const [, y, mo, d] = m;
  const names = MONTHS[(language ?? "en").slice(0, 2).toLowerCase()];
  if (!names) return iso;
  const month = names[Number(mo) - 1];
  const day = Number(d);
  return language.slice(0, 2).toLowerCase() === "de"
    ? `${day}. ${month} ${y}`
    : `${day} ${month} ${y}`;
}

/** Words, the way the shorten budget counts them. */
export function countWords(text: string): number {
  return (text ?? "").trim() ? text.trim().split(/\s+/).length : 0;
}
```

### 5. `frontend/src/lib/render/letter-fit.ts` (new)

```ts
/**
 * Where the letter stops fitting page 1, and how much to ask the model to cut.
 *
 * react-pdf will not tell you *where* it broke — but page count is monotone in body
 * length, so a binary search over WORD boundaries (never mid-word: a half word re-wraps
 * differently and the index would lie) finds the last word that still fits in ~log₂(n)
 * renders. For a 250-word letter that's 8 renders, about a second, run once when the
 * preflight reports an overflow.
 */
export type FitIndex = {
  /** Character index in the body: everything from here on does not fit page 1. */
  index: number;
  /** Words that do fit. */
  words: number;
};

/** Character offset of the end of word `n` (1-based), or the whole string. */
export function wordBoundary(body: string, n: number): number {
  if (n <= 0) return 0;
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  let seen = 0;
  while ((m = re.exec(body))) {
    if (++seen === n) return m.index + m[0].length;
  }
  return body.length;
}

export async function fitIndex(
  body: string,
  pagesFor: (text: string) => Promise<number>,
  maxPages = 1,
): Promise<FitIndex | null> {
  const total = countWords(body);
  if (total === 0) return null;
  if ((await pagesFor(body)) <= maxPages) return null; // it already fits

  let lo = 0; // fits
  let hi = total; // does not
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    const p = await pagesFor(body.slice(0, wordBoundary(body, mid)));
    if (p <= maxPages) lo = mid;
    else hi = mid;
  }
  return { index: wordBoundary(body, lo), words: lo };
}

/**
 * The word budget to ask for. Derived from the measurement, not a flat percentage: we know
 * how many words fit, so aim just under it. The 0.95 is slack for the model overshooting
 * and for the closing furniture.
 */
export function shortenTarget(fit: FitIndex): number {
  return Math.max(60, Math.floor(fit.words * 0.95));
}
```

(`countWords` imported from `@/lib/letter-doc`.)

### 6. `frontend/src/lib/render/templates.tsx` — `letterStyles` + `LetterPage`

**a.** honour the flag (line 267):

```tsx
function letterStyles(spec: LayoutSpec) {
  const base = Math.max(spec.font.base_pt, 11); // letters read better a notch larger
  // DIN 5008 form B puts the address at 45mm so it shows through a window envelope. A
  // letter that only ever goes out as a PDF attachment doesn't need the window — and the
  // offset costs ~25mm of page 1, which is ~4 lines of prose.
  const din = spec.cover_letter.din5008;
  const addressTop = din ? mm(45) : mm(20);
  const bodyTop = din ? mm(85) : mm(58);
  return StyleSheet.create({
    page: {
      paddingTop: bodyTop,
      …
    },
    addressField: {
      position: "absolute",
      top: addressTop,
      left: mm(25),
      width: mm(85),
    },
    date: {
      position: "absolute",
      top: addressTop,
      right: mm(20),
      fontSize: base, // was base * 0.9 — it is a date, not a footnote
    },
```

**b.** the page-2 repeat (lines 358 and 366) — drop `fixed` from **both**:

```tsx
      <View style={styles.addressField}>
```
```tsx
      <Text style={styles.date}>{dateLine}</Text>
```

Safe because both are *top*-anchored absolutes: they can't trigger the trailing-blank-page
behaviour the `HiddenInk` comment warns about (that needs a bottom anchor). The contact `footer`
keeps its `fixed` — a footer *should* repeat.

**c.** the formatted date (line 351):

```tsx
  const dateLine = [snd.city, formatLetterDate(meta.date, meta.language)]
    .filter(Boolean)
    .join(", ");
```

### 7. `frontend/src/lib/queries/applications.ts`

`useShortenLetter`, modelled on `useRewriteParagraph` right above it:

```ts
export function useShortenLetter() {
  return useMutation({
    mutationFn: ({
      id,
      body,
      targetWords,
      language,
    }: {
      id: number;
      body: string;
      targetWords: number;
      language: string;
    }) =>
      api<{ body: string }>(`/api/jac/applications/${id}/shorten/`, {
        method: "POST",
        body: JSON.stringify({
          body,
          target_words: targetWords,
          language,
        }),
      }),
  });
}
```

### 8. `frontend/src/components/applications/highlighted-textarea.tsx` (new)

```tsx
/**
 * A textarea with a highlighted tail. A textarea can't style ranges, so this is the
 * standard mirror trick: an absolutely positioned div renders the same text with the
 * overflow wrapped in a <mark>, and the textarea sits on top with a transparent
 * background. The two MUST share every metric that affects wrapping — font, size, line
 * height, padding, border width, width — or the highlight drifts from the text.
 *
 * If the mirror turns out to drift in practice (log it in Results), the fallback is a
 * read-only "this will not fit on page 1" block under the textarea: less pretty, zero
 * alignment risk.
 */
import { useRef, type ChangeEvent } from "react";
import { cn } from "@/lib/utils";

const SHARED =
  "w-full px-3 py-2 text-sm leading-relaxed font-sans whitespace-pre-wrap break-words";

export function HighlightedTextarea({
  value,
  onChange,
  overflowAt,
  rows = 14,
  className,
  textareaRef,
  onSelect,
}: {
  value: string;
  onChange: (v: string) => void;
  /** Character index where the text stops fitting, or null. */
  overflowAt: number | null;
  rows?: number;
  className?: string;
  textareaRef?: React.RefObject<HTMLTextAreaElement | null>;
  onSelect?: () => void;
}) {
  const mirror = useRef<HTMLDivElement>(null);
  const own = useRef<HTMLTextAreaElement>(null);
  const ref = textareaRef ?? own;
  const cut = overflowAt != null && overflowAt < value.length ? overflowAt : null;

  return (
    <div className={cn("relative", className)}>
      <div
        ref={mirror}
        aria-hidden
        className={cn(SHARED, "pointer-events-none absolute inset-0 overflow-hidden")}
      >
        {cut == null ? (
          value
        ) : (
          <>
            {value.slice(0, cut)}
            <mark className="bg-amber-200/70 text-foreground dark:bg-amber-500/30">
              {value.slice(cut)}
            </mark>
          </>
        )}
      </div>
      <textarea
        ref={ref}
        rows={rows}
        value={value}
        onChange={(e: ChangeEvent<HTMLTextAreaElement>) => onChange(e.target.value)}
        onSelect={onSelect}
        onScroll={() => {
          if (mirror.current && ref.current)
            mirror.current.scrollTop = ref.current.scrollTop;
        }}
        className={cn(
          SHARED,
          "relative resize-y rounded-md border bg-transparent outline-none",
        )}
      />
    </div>
  );
}
```

### 9. `frontend/src/components/applications/letter-editor.tsx`

Two new props (`letterPages`, and a `spec` for measuring), the overflow measurement, and the
shorten loop:

```tsx
  const shorten = useShortenLetter();
  const [overflow, setOverflow] = useState<FitIndex | null>(null);
  const [shortening, setShortening] = useState(false);

  // Only ever measured when the preflight already says it spills — the binary search is
  // ~8 renders, too expensive to run speculatively.
  useEffect(() => {
    if (!spec || letterPages == null || letterPages <= 1) {
      setOverflow(null);
      return;
    }
    let cancelled = false;
    void fitIndex(body, (text) =>
      pdfPages(LetterDocument({ spec, meta, body: text })),
    ).then((f) => !cancelled && setOverflow(f));
    return () => {
      cancelled = true;
    };
  }, [spec, letterPages, body, meta]);

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
      for (let attempt = 0; attempt < 3 && fit; attempt++) {
        const res = await shorten.mutateAsync({
          id: applicationId,
          body: text,
          targetWords: shortenTarget(fit),
          language: meta.language,
        });
        if (!res.body) break; // model failed — keep what we had
        text = res.body;
        fit = await fitIndex(text, (t) =>
          pdfPages(LetterDocument({ spec, meta, body: t })),
        );
      }
      onBody(text);
      if (fit) toast.warning("Still a little long — trim the highlighted part by hand.");
      else toast.success("The letter fits on one page.");
    } catch {
      toast.error("Could not shorten the letter");
    } finally {
      setShortening(false);
    }
  }
```

and in the JSX, the body `<Textarea>` is replaced by `<HighlightedTextarea …
overflowAt={overflow?.index ?? null} textareaRef={bodyRef} onSelect={onBodySelect} />` — keep the
existing `bodyRef` and `onBodySelect` wiring, the selection popover must keep working — with the
action under it:

```tsx
      {letterPages != null && letterPages > 1 && (
        <div className="flex items-center gap-2">
          <p className="text-xs text-destructive">
            {overflow
              ? `About ${countWords(body) - overflow.words} words too long — the highlighted tail runs onto page ${letterPages}.`
              : `This letter runs to ${letterPages} pages.`}
          </p>
          <Button
            size="sm"
            variant="outline"
            disabled={!overflow || shortening}
            onClick={onShortenToFit}
          >
            {shortening ? "Shortening…" : "Shorten to fit"}
          </Button>
        </div>
      )}
```

## Tests

**Step 0 — unskip.** Delete the `@skip` in the backend file and every `.skip` in the frontend ones.

| file | covers |
| --- | --- |
| `backend/jac/tests/test_prompts.py` | `CoverLetterWriter` target band is inside what a DIN page holds and is overridable per call; `ShortenLetter` puts the target, the current count and the paragraph count in the prompt, keeps a blank-line paragraph split, returns `""` on a blank body and on an LLM failure, and floors the target at 60. |
| `backend/jac/tests/test_api.py` | the shorten endpoint: owner-only, 400 on a missing/absurd `target_words`, returns `{"body": …}`. |
| `frontend/tests/lib/letter-doc.test.ts` | `formatLetterDate` (de long form, en long form, unknown language → ISO, malformed → passthrough), `countWords`. |
| `frontend/tests/lib/letter-fit.test.ts` | **new** — `wordBoundary` (never lands mid-word, clamps at both ends), `fitIndex` (null when it already fits, exact boundary, ~log₂ renders not n), `shortenTarget` (just under what fits, floored at 60). |

```bash
cd backend && python manage.py test jac.tests.test_prompts jac.tests.test_api
cd frontend && npx vitest run tests/lib/letter-doc.test.ts tests/lib/letter-fit.test.ts
```

## Verification

1. Suites red → green; `npx tsc -b`; `python manage.py check`.
2. Generate a fresh letter. It should now come back at ~170–230 words and **fit on one page
   without any shortening** — that is the actual fix; everything below is the safety net.
3. Paste a deliberately long body (400+ words). Within a second the editor says "About N words too
   long" and the tail is highlighted. Scroll the textarea — the highlight must scroll with the text.
4. **Shorten to fit** → the letter comes back shorter, **with the same number of paragraphs**
   (this is the reported bug: verify the paragraph count before and after).
5. Repeat with an absurdly long body (800 words) — it should take up to 3 attempts and then warn
   rather than loop.
6. Force a 2-page letter and export it: page 2 must **not** repeat the recipient address or the
   date.
7. Set `"din5008": false` in a copy of the layout JSON, seed it, switch the application to it: the
   address block moves up, the body starts higher, and roughly 4 more lines fit.
8. Switch the letter language to `de` and re-export: the date reads `Berlin, 27. Juli 2026`. An
   unknown language code falls back to ISO, never to a US month-first date.

## Results

<!-- human: raw test output, observed issues, what works -->
