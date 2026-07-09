# [backend] application-content-v2 — structured letter, persisted address, two standard layouts

> Guide 1 of 4 for the "frontend polish" phase (roadmap: pipeline-to-frontend follow-up; target
> flow: job post → application → layout → optional AI run / manual dump → manual customisation →
> frontend PDF). Branch: `backend/application-content-v2`. **Land and merge this first** — guides
> 2–4 (`frontend/cv-editor`, `frontend/letter-editor`, `frontend/render-export`) build on the
> serializer contract defined here.

## Context / goal

The frontend is taking over rendering and export (md/json/pdf via `@react-pdf/renderer` — decided
earlier, see memory `cv-render-export-decision`). Reading the code, three backend gaps block that:

1. **The extracted `JobPostAddress` is never persisted.** `generate_run` builds an _unsaved_
   `JobPostAddress(**fields)` (jac/tasks.py step 2), feeds it to `CoverLetter`, and drops it. The
   recipient block survives only inside `run.result`. `CoverLetter.__init__` even tries
   `job_posting.address` as a fallback (cover_letter.py:150-156) — the reverse 1:1 that no code
   ever creates. Persist it so the SPA can prefill the letter recipient without digging through
   old runs.
2. **Applying a run flattens the letter to text.** `JobApplication.cover_letter` gets
   `result["text"]` — sender block, recipient, date, subject, salutation, body, closing, name,
   all in one string. A DIN-5008 PDF needs those pieces separately (fixed address-window
   position, right-aligned date, bold subject). New `letter_meta` JSONField holds the furniture;
   `cover_letter` becomes **just the editable body** (body + personal paragraph). The full text
   stays reproducible (frontend re-assembles it for the md export, mirroring
   `CoverLetter.render_markdown`).
   - `build()` result additionally exposes `closing` (the `_CLOSING` furniture line) — it is
     currently baked into `text` only, and the frontend must not hardcode the bilingual strings.
3. **Only one system layout.** The user wants two standards: one-page and two-page CV. Add
   `two_page_layout.json` and generalize the seeder. Also fix the existing
   `default_layout.json`: its `cv.sections` says `"education"` but every other contract
   (`cv_content` keys, `/api/jac/cv/entries/` response, `CvRender.SECTION_ORDER`) uses the plural
   `"educations"` — align it before the frontend starts consuming the spec.
4. **No way to AI-rewrite an edited passage.** The letter editor (guide 3) wants a "rewrite this
   selection" action — e.g. to polish the hand-written personal paragraph. That needs one small
   endpoint: `POST /api/jac/applications/<pk>/rewrite/` with `{text, instruction?, alias?}` →
   `{text}`. New `ParagraphRewrite` prompt class in `llm_prompts.py`, same fabrication rules as
   `CoverLetterWriter` (the passage is authoritative, the posting is NOT given). Note the
   trade-off: this is a **synchronous** LLM call in the request cycle — fine for a one-paragraph
   rewrite on a personal tool, and much simpler than a run + WebSocket round-trip; if it ever
   grows past a paragraph, move it onto the Celery path.

**No new "dump" endpoint is needed** for the no-AI manual mode: `/api/jac/cv/entries/`
(`CVEntryListView`, all career entries grouped by type) and `/api/jac/resume-snippets/` already
exist and are user-scoped. Guide 2 consumes them as-is.

## Affected files

| file                                                      | why                                                                                                                 |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `backend/jac/models.py`                                   | `JobApplication.letter_meta` JSONField                                                                              |
| `backend/jac/migrations/00XX_…`                           | `makemigrations jac` output for the new field                                                                       |
| `backend/jac/serializers.py`                              | `JobPostAddressSerializer`, nested `address` on `JobPostingSerializer`, `letter_meta` on `JobApplicationSerializer` |
| `backend/jac/cover_letter.py`                             | expose `closing` in `build()` result + `editable_body()` helper                                                     |
| `backend/jac/tasks.py`                                    | persist the address (`update_or_create`); auto-fill `letter_meta` + body-only `cover_letter`                        |
| `backend/jac/resources/default_layout.json`               | `"education"` → `"educations"`                                                                                      |
| `backend/jac/resources/two_page_layout.json`              | new two-page layout spec                                                                                            |
| `backend/jac/management/commands/seed_default_domains.py` | seed both layouts; refresh a template when the resource file changed                                                |
| `backend/jac/llm_prompts.py`                              | `ParagraphRewrite` — rewrite one passage, no fact invention                                                         |
| `backend/jac/views.py`                                    | `rewrite` action on `JobApplicationViewSet`                                                                         |

## The code

### 1. `backend/jac/models.py` — letter_meta

In `JobApplication`, directly under `cover_letter`:

```python
    cv_content = models.JSONField(default=dict, blank=True)
    cover_letter = models.TextField(blank=True)
    # Structured letter furniture for the frontend PDF/md render: language, subject,
    # salutation, date, closing, sender/recipient blocks. Same contract as cv_content —
    # an SPA-owned JSON artefact the backend stores but never interprets. Filled from a
    # run's cover_letter result on apply/auto-fill; typed by hand in manual mode.
    letter_meta = models.JSONField(default=dict, blank=True)
```

Then:

```bash
python manage.py makemigrations jac && python manage.py migrate
```

### 2. `backend/jac/cover_letter.py` — closing + editable body

Add `closing` to the result dict in `build()` (next to `date`):

```python
            "date": timezone.localdate().isoformat(),
            "closing": _CLOSING.get(language, _CLOSING["en"]),
```

And in `render_markdown`, use the already-computed value instead of re-deriving (keeps one source
of truth; `r["closing"]` is always present because only `build()` calls it):

```python
        out.append(r["closing"])
```

New module-level helper (put it next to `PERSONAL_STUB`, above the classes) — the single
definition of "what lands in the editable `JobApplication.cover_letter`"; the SPA mirrors it in
`letter-doc.ts` (guide 3):

```python
def editable_body(letter: dict) -> str:
    """The sendable middle of a built letter: body + personal paragraph (real or stub).

    This — not the fully furnished `text` — is what belongs in the editable
    `JobApplication.cover_letter`; subject/salutation/date/closing/addresses live in
    `letter_meta` and are re-assembled at render/export time.
    """
    parts = [letter.get("body", "")]
    if letter.get("personal_paragraph"):
        parts.append(letter["personal_paragraph"])
    return "\n\n".join(p for p in parts if p)
```

### 3. `backend/jac/tasks.py` — persist address, richer auto-fill

Import the helper:

```python
from jac.cover_letter import CoverLetter, editable_body
```

Step 2 of the pipeline — replace the unsaved instance with a persisted row (`update_or_create`
because re-runs re-extract; the 1:1 must update, not duplicate):

```python
            # 2. Extract the recipient address; refresh the persisted JobPosting.
            _progress(run, "reading posting")
            extracted = AddressExtract(jp.posting_text, alias=alias, user=user).extract()
            jp.title = extracted.get("title", "") or jp.title
            jp.language = extracted.get("language", "en") or "en"
            jp.save(update_fields=["title", "language", "updated_at"])
            addr, _ = JobPostAddress.objects.update_or_create(
                job_posting=jp,
                defaults={f: extracted.get(f, "") for f in _ADDRESS_FIELDS},
            )
```

(`addr` still flows into `CoverLetter(..., address=addr, ...)` unchanged.)

The auto-fill block — body-only letter + the furniture. The keys are pulled explicitly so a
future result-shape change can't smuggle arbitrary blobs into `letter_meta`:

```python
        # Auto-fill the application only while it's still untouched; afterwards the user
        # applies a run's result explicitly from the SPA, so re-runs never clobber edits.
        if not application.cv_content and not application.cover_letter:
            application.cv_content = result["cv"]
            application.cover_letter = editable_body(letter)
            application.letter_meta = {
                k: letter[k]
                for k in ("language", "subject", "salutation", "date", "closing",
                          "sender", "recipient")
                if k in letter
            }
            application.save(
                update_fields=["cv_content", "cover_letter", "letter_meta", "updated_at"]
            )
```

### 4. `backend/jac/serializers.py` — address out, letter_meta through

New serializer (place it above `JobPostingSerializer`):

```python
class JobPostAddressSerializer(serializers.ModelSerializer):
    """Read-only nested shape for the extracted employer address."""

    class Meta:
        model = JobPostAddress
        fields = [
            "company", "contact_name", "street", "address_line2",
            "zip", "city", "country", "email", "phone",
        ]
        read_only_fields = fields
```

(add `JobPostAddress` to the models import at the top.)

`JobPostingSerializer` — nested, `None` while no run has extracted one. A `SerializerMethodField`
because the reverse 1:1 raises when absent; `getattr`'s default catches it
(`RelatedObjectDoesNotExist` subclasses `AttributeError`):

```python
class JobPostingSerializer(serializers.ModelSerializer):
    address = serializers.SerializerMethodField()

    class Meta:
        model = JobPosting
        fields = [
            "id", "title", "posting_text", "language", "source_url",
            "address", "active", "created_at", "updated_at",
        ]
        read_only_fields = fields

    def get_address(self, obj) -> dict | None:
        addr = getattr(obj, "address", None)
        return JobPostAddressSerializer(addr).data if addr else None
```

`JobApplicationSerializer.Meta.fields` — add `"letter_meta"` right after `"cover_letter"`.
It stays writable (like `cv_content`); nothing else changes.

### 5. `backend/jac/resources/default_layout.json` — plural section key

```json
{
  "version": 1,
  "page": { "size": "A4", "margin": [56, 48] },
  "font": { "family": "Helvetica", "base_pt": 10 },
  "colors": { "accent": "#1a5fb4", "text": "#1c1c1c", "muted": "#6b6b6b" },
  "cv": {
    "pages": 1,
    "sections": ["jobs", "educations", "projects", "certifications"],
    "sidebar": ["skills", "languages"]
  },
  "cover_letter": { "din5008": true }
}
```

### 6. `backend/jac/resources/two_page_layout.json` — new

Same skeleton, two pages, slightly larger base font (a two-pager doesn't need to squeeze):

```json
{
  "version": 1,
  "page": { "size": "A4", "margin": [56, 48] },
  "font": { "family": "Helvetica", "base_pt": 11 },
  "colors": { "accent": "#1a5fb4", "text": "#1c1c1c", "muted": "#6b6b6b" },
  "cv": {
    "pages": 2,
    "sections": ["jobs", "educations", "projects", "certifications"],
    "sidebar": ["skills", "languages"]
  },
  "cover_letter": { "din5008": true }
}
```

### 7. `backend/jac/management/commands/seed_default_domains.py` — both layouts, refresh-on-change

Replace the single-layout constants:

```python
RESOURCES = Path(__file__).resolve().parents[2] / "resources"

# (name, resource file) per shared system layout. The seeder refreshes a layout's template
# whenever the resource file content changed, so editing a JSON spec + re-running the seed
# is the whole deploy story for layout tweaks.
DEFAULT_LAYOUTS = [
    ("default", RESOURCES / "default_layout.json"),
    ("two-page", RESOURCES / "two_page_layout.json"),
]
```

and the layout block in `handle()`:

```python
        for name, resource in DEFAULT_LAYOUTS:
            layout, layout_created = ApplicationLayout.objects.get_or_create(
                user=system, name=name
            )
            data = resource.read_bytes()
            if layout.template:
                with layout.template.open("rb") as fh:
                    current = fh.read()
            else:
                current = None
            if current != data:
                if layout.template:
                    layout.template.delete(save=False)
                layout.template.save(resource.name, ContentFile(data))
                layout_action = "created" if layout_created else "template refreshed"
            else:
                layout_action = "unchanged"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Layout {layout.name!r}: {layout_action} ({layout.template.name})"
                )
            )
```

(drop the old single-layout block and its final stdout line; `DEFAULT_LAYOUT_NAME` /
`DEFAULT_LAYOUT_TEMPLATE` go away — nothing else imports them.)

Note the behaviour change: the seeder now _refreshes_ a stale template (the old code only
attached one if missing). That is what makes the `default_layout.json` plural-fix in step 5
actually reach existing dev DBs.

### 8. `backend/jac/llm_prompts.py` — `ParagraphRewrite`

Place it after `CoverLetterWriter` (it's the same family). Free prose out — like the writer,
this is a place where line-format I/O does not apply:

```python
class ParagraphRewrite:
    """Rewrite ONE user-selected passage of a cover-letter body on demand.

    The SPA's letter editor sends the passage plus an optional instruction ("shorter", "more
    formal", …). The passage is authoritative — the model rephrases, it does not add facts
    (same fabrication rule as CoverLetterWriter, and the posting is again NOT given).
    Free prose out; any failure -> '' so the caller keeps the original text.
    """

    _INSTRUCTION = (
        "Rewrite the passage below from a cover letter. Keep the meaning and every factual "
        "claim — do not add skills, employers, job titles, numbers, dates, or achievements "
        "the passage does not state. Keep roughly the same length unless the request says "
        "otherwise. Write in {language}. Output ONLY the rewritten passage — no quotes, no "
        "markdown, no commentary."
    )
    _MAX_CHARS = 4000  # a passage, not a document — the view 400s above this

    def __init__(
        self,
        passage: str,
        *,
        instruction: str = "",
        language: str = "en",
        alias: str = "default",
        user=None,
    ):
        self.passage = passage
        self.instruction = instruction
        self.language = language
        self.alias = alias
        self.user = user

    def rewrite(self) -> str:
        """Return the rewritten passage. '' on blank input or any LLM failure."""
        if not self.passage.strip():
            return ""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("ParagraphRewrite: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        req = f"REQUEST: {self.instruction}\n\n" if self.instruction else ""
        return (
            f"{self._INSTRUCTION.format(language=self.language)}\n\n"
            f"{req}PASSAGE:\n{self.passage}\n\nREWRITTEN:"
        )
```

### 9. `backend/jac/views.py` — the rewrite action

Imports: add `ParagraphRewrite` next to the `AddressExtract`-style imports
(`from jac.llm_prompts import ParagraphRewrite`). Then on `JobApplicationViewSet`:

```python
    @extend_schema(
        request=inline_serializer(
            "ParagraphRewrite",
            {
                "text": serializers.CharField(),
                "instruction": serializers.CharField(
                    required=False, allow_blank=True
                ),
                "alias": serializers.CharField(required=False),
            },
        ),
        responses=OpenApiResponse(description="{'text': rewritten passage}"),
    )
    @action(detail=True, methods=["post"])
    def rewrite(self, request, pk=None):
        """AI-rewrite one passage of the letter body. Only the passage travels both ways —
        the client splices the result back into its draft; nothing is persisted here.
        Synchronous LLM call (one paragraph; see the guide's decision note). 502 when the
        model yields nothing, so the client keeps the original text."""
        application = self.get_object()
        text = request.data.get("text") or ""
        if not text.strip():
            return Response(
                {"text": ["This field is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(text) > ParagraphRewrite._MAX_CHARS:
            return Response(
                {"text": ["Passage too long — select a paragraph, not the letter."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rewritten = ParagraphRewrite(
            text,
            instruction=(request.data.get("instruction") or "").strip(),
            language=application.posting.language or "en",
            alias=(request.data.get("alias") or "default").strip() or "default",
            user=request.user,
        ).rewrite()
        if not rewritten:
            return Response(
                {"detail": "The language model returned nothing — try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"text": rewritten})
```

(`self.get_object()` runs the user-scoped queryset + `IsOwner`, so another user's application
404s like everywhere else.)

## Tests (written by the AI, already on this branch — start red)

- `backend/jac/tests/test_generation_task.py` — extended `_LETTER` fixture (full furniture) +
  new tests: address persisted and updated (never duplicated) across runs; auto-fill writes the
  body-only `cover_letter` and the `letter_meta` furniture. The existing
  `test_done_autofills_empty_application` expectation is _updated_ to the body-only contract.
- `backend/jac/tests/test_job_application.py` — `letter_meta` defaults to `{}` and round-trips
  through PATCH; `posting_detail.address` is `null` before extraction and populated after.
- `backend/jac/tests/test_cover_letter.py` — `build()` exposes a language-correct `closing`;
  `editable_body()` composes body + personal paragraph and skips the empty paragraph.
- `backend/jac/tests/test_commands.py` — seeder creates _both_ layouts, the two-page spec says
  `pages == 2`, re-run is idempotent, and a changed resource refreshes the stored template.
- `backend/jac/tests/test_llm_rungs.py` — `ParagraphRewrite`: prompt carries passage +
  instruction + language and forbids invention; never sees a job posting; blank passage
  short-circuits without an LLM call; LLM failure returns `""`.
- `backend/jac/tests/test_job_application.py` (`RewriteEndpointTests`) — happy path 200,
  blank text 400, over-long passage 400, empty model reply 502, foreign application 404.

Run them (pyenv `jac` env, from `backend/`):

```bash
python manage.py test jac.tests.test_generation_task jac.tests.test_job_application \
    jac.tests.test_cover_letter jac.tests.test_commands jac.tests.test_llm_rungs
```

## Verification

1. `python manage.py makemigrations jac && python manage.py migrate` — one new migration, adds
   `letter_meta`.
2. `python manage.py seed_default_domains` — output lists both layouts; run again → both
   `unchanged`.
3. Test suite above: red → green. Then the full `python manage.py test jac` stays a clean wall
   of dots.
4. Live smoke (dev stack up, README "Run (dev)"): trigger a generation on a fresh application.
   After `done`:
   - `GET /api/jac/applications/<pk>/` → `cover_letter` contains only the letter body (no
     address block), `letter_meta` has subject/salutation/date/closing/sender/recipient, and
     `posting_detail.address.company` is filled.
   - re-running keeps exactly one `JobPostAddress` row for the posting (check admin or shell).
5. Rewrite smoke (Ollama up): `POST /api/jac/applications/<pk>/rewrite/` with
   `{"text": "I did stuff at my job.", "instruction": "more formal"}` → 200 with a rephrased
   `text`; with Ollama stopped → 502, not a 500 traceback.

## Results

two errors after testing

```bash
lukas@localhost backend % ./manage.py test jac.tests.test_generation_task jac.tests.test_job_application \
    jac.tests.test_cover_letter jac.tests.test_commands jac.tests.test_llm_rungs
Found 143 test(s).
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
...............EE..............................................................................................................................
======================================================================
ERROR: test_letter_meta_defaults_empty_and_roundtrips (jac.tests.test_job_application.JobApplicationContentV2Tests.test_letter_meta_defaults_empty_and_roundtrips)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/lukas/Projects/jac/backend/jac/tests/test_job_application.py", line 186, in test_letter_meta_defaults_empty_and_roundtrips
    self.assertEqual(r.data["letter_meta"], {})
                     ~~~~~~^^^^^^^^^^^^^^^
KeyError: 'letter_meta'

======================================================================
ERROR: test_posting_detail_address_null_until_extracted (jac.tests.test_job_application.JobApplicationContentV2Tests.test_posting_detail_address_null_until_extracted)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/lukas/Projects/jac/backend/jac/tests/test_job_application.py", line 209, in test_posting_detail_address_null_until_extracted
    self.assertIsNone(r.data["posting_detail"]["address"])
                      ~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^
KeyError: 'address'

----------------------------------------------------------------------
Ran 143 tests in 1.959s

FAILED (errors=2)
Destroying test database for alias 'default'...
lukas@localhost backend %
```
