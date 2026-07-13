# [fullstack] letter quality — MMR diversity, LetterCritic + repair, anti-summary standard, paragraph-as-opener

> **Mode note:** volatile — Lukas delegated implementation ("implement this for me",
> 2026-07-12) after the paragraph-as-opener extension; Claude wrote the source. Tests
> landed first (red) and went green with the implementation; Lukas owns the live
> verification below. Branch: `fullstack/letter-quality` (cut off a *dirty* main that
> still carries the uncommitted pipeline-v2 phase — commit or wrap that up
> before/with this work so the phases don't tangle in one commit).

## why

Pipeline v2 landed, and the Results verdicts were (see `[backend]-letter-pipeline-v2.md`):

- **strong**: "okayish, but very redundant — same experience mentioned in the same way
  multiple times. an overall analysis with a model would have shown that."
- **standard**: "only produces one short paragraph. takes the three snippets and
  summarizes them instead of connecting them."

Diagnosis (decisions cleared with Lukas 2026-07-12):

1. Redundancy starts **at selection**: the embedding pick is pure cosine-vs-posting
   top-3 — nothing stops three tellings of the same story from sweeping the top slots,
   and the writer then dutifully repeats it. Fix: an **MMR** (maximal marginal
   relevance) term on the body pick. Deterministic, zero extra LLM calls (the vectors
   are already computed), helps every grade including light glue.
2. There is **no quality feedback loop**: the strong audit checks truth, never prose.
   Fix: a **`LetterCritic`** rung (standard + strong) that flags redundancy / lost
   substance / compression / flow, feeding **one** repair rewrite through a generalized
   repair channel (the machinery the strong grounding repair already built).
3. The standard clause **invites compression** ("tighten wording, cut redundancy" reads
   as "summarize" to a small model). Fix: rewrite the clause (full letter, not a
   summary, ≈ combined snippet length, one paragraph per theme) **plus** a deterministic
   shrinkage backstop — a standard body under 0.6× the snippets' word count is a failed
   polish and triggers the repair even when the critic is clean or down.

Live findings from the personality questionnaire round (Lukas, 2026-07-12 — the
paragraph now generates for real, revealing two flaws):

4. **The paragraph sits at the wrong end.** It lands after the body, displacing the
   closing's call-to-action-and-thanks. Best structure: the personal "why this
   company" paragraph **opens** the letter, the bottom keeps a warm CTA + gratitude,
   and the closing arcs back to the opener to tie the letter together. Fix: `build()`
   creates the paragraph FIRST; the writer receives it as a context-only
   `OPENING PARAGRAPH` block (echo its theme in one closing clause, never repeat or
   mine it for facts); `editable_body`/`render_markdown`/frontend `editableBody` flip
   to paragraph-first.
5. **Brutal honesty reads as a liability.** The dossier's neutral truths ("values
   autonomy, speaks their mind") went into the paragraph verbatim — a hiring manager
   reads "independent and mouthy". The dossier stays honest (it serves the portfolio
   too); the **paragraph writer** learns the two-sides-of-the-coin rule: render every
   trait as the professional strength it implies (autonomy → works independently,
   delivers without hand-holding; speaks their mind → cares enough to speak up).
   `ParagraphGroundingCheck` correspondingly learns that a positively-reframed trait
   is supported by the underlying trait — reframing is not fabrication, or the audit
   would flag exactly the reframings we ask for.

## decisions

- **MMR, body pick only**: greedy; each next snippet maximises
  `0.7·relevance − 0.3·max-cosine-to-already-picked`. First pick = pure relevance.
  Intro/closing stay pure best-of-kind (never pick a worse intro just for diversity).
  Native-language stays the tiebreak. Structural fallback path untouched.
- **Critic grades**: standard + strong. Light stays glue-only (a 1B can't act on
  critique, and it would dilute the showcase rung). Runs on `verifier_alias or alias`
  (same rationale as `FaithfulnessCheck`).
- **Critique is advisory, grounding is safety**: after a repair rewrite the grounding is
  re-audited (when auditing is on) — the critique is *not* re-run; its `repaired: true`
  flag says "the flagged draft was replaced", not "verified fixed". Failure →
  `count: None` and the repair is simply skipped (nothing surfaced as "unchecked" —
  unlike grounding, no safety claim is being made).
- **One combined repair pass, never loops**: strong feeds grounding claims *and*
  critique notes into a single rewrite, then one grounding re-audit
  (`grounding.repaired` keeps its meaning). Standard repairs on critique notes only —
  its opt-in grounding stays **flag-only** (v2 decision preserved).
- **Shrinkage backstop is standard-only**: strong composes freely, its length varies by
  design; the polish contract is what implies preservation.
- Result dict gains `critique: {count, claims[, repaired]}`; frontend shows a quality
  badge next to the grounding badge.
- **Paragraph-as-opener**: only a REAL paragraph is fed to the writer as context — the
  stub never enters a prompt (`opening = "" if pp["is_stub"] else pp["text"]`). The
  stub still renders loudly, now at the top. The CTA-and-thanks close is pinned in the
  standard/strong clauses (light glue can't follow shape instructions); the arc-echo
  line rides inside the dynamic opening block, so it only appears when there is an
  opener to arc back to.
- **Result-dict shape unchanged** (`personal_paragraph` stays its own key) — only the
  *assembled* orders flip: `editable_body()`, `render_markdown()`, and the frontend
  mirror `editableBody()`.

## call order (for reading the tests' `side_effect` lists)

```
personal paragraph (research → write → opt-in check)   # FIRST now — it opens the letter
      → writer (opening as context) → grounding audit (strong always / opt-in)
      → critic (standard+strong)
      → [repair rewrite → grounding re-audit (iff auditing)]   # only when triggered
```

Strong worst case: 5 letter calls (was 4) + the paragraph calls when requested.
Standard default: 2 calls, 3 when the critic triggers a repair.

## affected files

| file                                             | change                                                                             |
| ------------------------------------------------ | ---------------------------------------------------------------------------------- |
| `backend/jac/llm_prompts.py`                     | `Embed.ranked_vectors()` + `_cos` → staticmethod; `LetterCritic`; writer clauses + `revision_notes` + `opening_paragraph`; `PersonalParagraphWriter`/`ParagraphGroundingCheck` instructions |
| `backend/jac/cover_letter.py`                    | `SnippetSelector` MMR body pick; `CoverLetter` critique/repair flow (replaces `_strong_repair`); `build()` reordered paragraph-first; `editable_body`/`render_markdown` flip |
| `frontend/src/lib/queries/generations.ts`        | `Critique` type, `critique` key on `CoverLetterResult`, `qualityBadge()`            |
| `frontend/src/lib/letter-doc.ts`                 | `editableBody()` mirror flips to paragraph-first                                    |
| `frontend/src/components/applications/generate-panel.tsx` | render the quality badge                                                  |
| `backend/jac/tests/test_llm_rungs.py`            | (AI, on disk) `LetterCriticTests`, writer/paragraph/check prompt extensions        |
| `backend/jac/tests/test_cover_letter.py`         | (AI, on disk) `SnippetSelectorMMRTests`, `CoverLetterCritiqueTests`, existing classes updated to the new call order, opener-order tests |
| `frontend/tests/lib/generations.test.ts`         | (AI, on disk) `qualityBadge` variants                                              |
| `frontend/tests/lib/letter-doc.test.ts`          | (AI, on disk) `editableBody` order flip                                            |

No model change, no migration, no serializer/tasks/WS change — `critique` rides inside
the existing `result.cover_letter` JSON exactly like `snippet_ranking` did.

## the code

### 1. `backend/jac/llm_prompts.py`

**1a. `Embed`** — split vector-keeping ranking out of `ranked_entries()` and make
`_cos` a staticmethod (the selector needs it for snippet-to-snippet similarity):

```python
    def ranked_entries(self) -> list[dict]:
        """rank the cv entries based on cosine similarity"""
        return [
            {"id": r["id"], "score": r["score"], "reason": ""}
            for r in self.ranked_vectors()
        ]

    def ranked_vectors(self) -> list[dict]:
        """Like ranked_entries, but keeps each entry's raw vector (`vec`) so callers
        can measure entry-to-entry similarity (the cover-letter MMR pick)."""
        vectors = self._query()

        if len(vectors) != len(self.entries) + 1:
            return []
        query_vec, doc_vecs = vectors[0], vectors[1:]
        return [
            {"id": e.get("id"), "score": self._cos(query_vec, dv), "vec": dv}
            for e, dv in zip(self.entries, doc_vecs)
        ]
```

```python
    @staticmethod
    def _cos(a, b) -> float:
        """Cosine similarity of two vectors. 0.0 if either is empty/zero-norm."""
        d = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return d / (na * nb) if na and nb else 0.0
```

(Existing instance-style calls `self._cos(...)` keep working.)

**1b. `CoverLetterWriter._GRADE_CLAUSE`** — replace `standard` and `strong`:

```python
        "standard": (
            "Rework the snippets into a polished, cohesive letter body. This is a full "
            "letter, not a summary: keep every concrete claim, keep roughly the combined "
            "length of the snippets, and write one paragraph per theme with real "
            "transitions. Reorder for flow and tighten wording where it repeats — but "
            "never compress the substance away. Close with a brief final paragraph: a "
            "call to action and genuine thanks for the consideration. Do not invent "
            "facts the snippets do not state."
        ),
        "strong": (
            "Compose an original, persuasive letter body tailored to THIS job posting. Use the "
            "posting only to choose emphasis, ordering, and tone — the posting is NEVER a "
            "source of facts about the candidate. Every factual claim (skills, employers, "
            "titles, numbers, dates, achievements) must come from the snippets — invent "
            "nothing. State each experience, project, and achievement at most once — never "
            "retell the same fact in different words. Close with a brief final paragraph: "
            "a call to action and genuine thanks for the consideration."
        ),
```

**1c. `CoverLetterWriter`** — two new kwargs: `revision_notes` (the critique repair
channel, parallel to `unsupported_claims`) and `opening_paragraph` (the already-written
personal paragraph that now OPENS the letter — context, never a fact source). `__init__`
signature gains the last parameters:

```python
        posting_text: str = "",
        unsupported_claims: list[str] | None = None,
        revision_notes: list[str] | None = None,
        opening_paragraph: str = "",
    ):
```

body of `__init__` gains:

```python
        self.revision_notes = revision_notes or []
        self.opening_paragraph = opening_paragraph
```

and `_prompt()` gets `opening` + `notes` blocks (return line changes too). The
arc-echo instruction lives inside the dynamic block, so it only exists when there is
an opener to arc back to:

```python
        opening = ""
        if self.opening_paragraph:
            opening = (
                "OPENING PARAGRAPH (already written; it appears directly above your "
                "text): context only, never a source of facts about the candidate, and "
                "do not repeat it — but you may echo its theme in one clause of your "
                "closing to tie the letter together.\n"
                f"{self.opening_paragraph}\n\n"
            )
        repair = ""
        if self.unsupported_claims:
            claims = "\n".join(f"- {c}" for c in self.unsupported_claims)
            repair = (
                "A previous draft contained these unsupported claims — remove them or "
                f"replace them with claims the snippets actually state:\n{claims}\n\n"
            )
        notes = ""
        if self.revision_notes:
            flagged = "\n".join(f"- {n}" for n in self.revision_notes)
            notes = (
                "A reviewer flagged these writing problems in the previous draft — fix "
                f"them without inventing new facts:\n{flagged}\n\n"
            )
        return (
            f"{clause}\n{common}\n\n"
            f"CANDIDATE: {self.candidate_name}\n"
            f"ROLE: {self.title}\n\n"
            f"{opening}{posting}{repair}{notes}"
            f"SNIPPETS (your only source of facts):\n{blocks}\n\nLETTER BODY:"
        )
```

Also update the class docstring's last paragraph to mention the channels:

```python
    `unsupported_claims` and `revision_notes` are the repair-pass channels: the
    grounding audit's findings and the LetterCritic's writing notes are fed back so
    one rewrite can fix both. `opening_paragraph` is the personal paragraph that will
    sit above this body — context for the arc, never a source of candidate facts.
```

**1d. `LetterCritic`** — new class, place directly **after `FaithfulnessCheck`**
(it mirrors it and shares `_parse_unsupported`):

```python
class LetterCritic:
    """Prose-quality reviewer for a generated cover-letter body: reads the snippets and
    the body and flags WRITING problems — redundancy, lost substance, compression, flow.

    Advisory, not safety: findings feed ONE repair rewrite (CoverLetterWriter's
    `revision_notes`), and on any failure the caller just skips the repair —
    count=None is never surfaced as "unchecked" the way the grounding audit's is,
    because no safety claim is being made. Faithfulness stays FaithfulnessCheck's job;
    this rung is told not to fact-check. Same line format, same shared parser, same
    honesty rule (listed lines win, unreadable -> None) — see the `no-json-llm-io`
    memory.
    """

    _INSTRUCTION = (
        "You are reviewing the BODY of a job-application cover letter that was written "
        "from the candidate's authored SNIPPETS.\n"
        "Flag WRITING-QUALITY problems only:\n"
        "  - redundancy: the same experience, achievement, or fact told more than once;\n"
        "  - lost substance: a concrete snippet claim (skill, employer, number, "
        "achievement) missing from the body;\n"
        "  - compression: the body reads like a summary of the snippets instead of a "
        "full letter;\n"
        "  - flow: abrupt jumps, paragraphs without connective tissue.\n"
        "Do NOT fact-check (a separate audit does that) and do NOT flag tone or opinion.\n"
        "Reply in this EXACT line format, nothing else:\n"
        "  - first line: 'ISSUES <n>' — the number of problems (0 if none);\n"
        "  - then ONE line per problem, '- <issue>' (<=20 words), worst first;\n"
        "  - if the body is sound, write 'ISSUES 0' and nothing else.\n"
        "No prose, no markdown headers, no JSON."
    )

    _COUNT_RE = re.compile(r"\bISSUES\s+(\d+)\b", re.IGNORECASE)
    _CLAIM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)")

    def __init__(self, body: str, snippets: list, user=None, alias: str = "default"):
        self.body = body
        self.snippets = snippets
        self.user = user
        self.alias = alias

    def critique(self) -> dict:
        """Return {'count': int | None, 'claims': [str]}. None = critic unavailable —
        the caller skips the repair, nothing more."""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("LetterCritic: LLM call failed")
            return {"count": None, "claims": []}
        return _parse_unsupported(raw, self._COUNT_RE, self._CLAIM_RE)

    def _prompt(self) -> str:
        blocks = (
            "\n\n".join(
                f"[{s.get_kind_display()}] {s.title}\n{s.content}"
                for s in self.snippets
            )
            or "(no snippets)"
        )
        return (
            f"{self._INSTRUCTION}\n\n"
            f"SNIPPETS (what the body was written from):\n{blocks}\n\n"
            f"LETTER BODY:\n{self.body}\n\n"
            f"REVIEW:"
        )
```

**1e. `PersonalParagraphWriter._INSTRUCTION`** — replace entirely (opener role +
the two-sides-of-the-coin framing rule):

```python
    _INSTRUCTION = (
        "Write ONE short paragraph (3-5 sentences) that OPENS a cover letter: why this "
        "candidate is personally drawn to and a strong fit for THIS company. Connect a "
        "specific thing about the company (from RESEARCH) to who the candidate is (from "
        "PERSONALITY). Use ONLY facts from RESEARCH for company claims and ONLY traits "
        "from PERSONALITY for the candidate — invent nothing, add no skills/employers/"
        "numbers. Every trait is one side of a coin: render each PERSONALITY trait as "
        "the professional strength it implies (values autonomy -> works independently "
        "and delivers without hand-holding; speaks their mind -> cares enough to speak "
        "up) — never word one so it could read as a liability. First person, genuine, "
        "not fawning. End on a short bridge that leads naturally into the "
        "qualifications below. No salutation, no sign-off, no markdown, no headers — "
        "just the paragraph."
    )
```

Also update the class docstring's first line ("Write ONE cover-letter paragraph…" →
"…the OPENING paragraph of a cover letter…").

**1f. `ParagraphGroundingCheck._INSTRUCTION`** — one sentence added after the
UNSUPPORTED definition, so the audit doesn't flag exactly the reframings 1e asks for:

```python
    _INSTRUCTION = (
        "You are fact-checking a cover-letter PARAGRAPH against two sources: RESEARCH (company facts) "
        "and PERSONALITY (the candidate). A claim is UNSUPPORTED if neither source states or clearly "
        "implies it. A PERSONALITY trait rendered in a positive professional light is supported by "
        "the underlying trait — reframing is not fabrication. List every unsupported factual claim.\n"
        "Reply in this EXACT line format, nothing else:\n"
        "  - first line: 'UNSUPPORTED <n>';\n"
        "  - then ONE line per claim, '- <claim>' (<=20 words), worst first;\n"
        "  - if all grounded, write 'UNSUPPORTED 0' and nothing else.\n"
        "Do not flag tone, opinion, or first-person framing — only checkable facts. No prose, no JSON."
    )
```

### 2. `backend/jac/cover_letter.py`

**2a. import** — add `LetterCritic` to the `jac.llm_prompts` import block.

**2b. `SnippetSelector`** — MMR body pick on the embedding path. Add the class attr
next to `_BODY_KINDS`:

```python
    # MMR: relevance weight on the greedy body pick; (1-λ) penalises similarity to
    # what is already picked, so three tellings of one story can't sweep the top-3.
    _MMR_LAMBDA = 0.7
```

`_embed_scores` now keeps the vectors (docstring + the two changed lines):

```python
    def _embed_scores(self, active: list) -> dict | None:
        """{snippet id: {'score': cosine vs the posting, 'vec': raw vector}} via the
        first embed-capable alias in the chain, or None when nothing can embed. Failure
        walks the chain instead of raising — a letter must never die because an
        embedder is down."""
        if not active or not self.posting_text:
            return None
        entries = [{"id": self._sid(s), "text": s.content} for s in active]
        tried: list[str] = []
        for alias in (self.embed_alias, self.alias, "default"):
            if not alias or alias in tried:
                continue
            tried.append(alias)
            try:
                ranked = SnippetEmbed(
                    self.posting_text, entries, user=self.user, alias=alias
                ).ranked_vectors()
            except Exception as exc:  # noqa: BLE001 — walk the chain on any failure
                logger.info("snippet embedding via %r unavailable: %s", alias, exc)
                continue
            if ranked:
                return {r["id"]: {"score": r["score"], "vec": r["vec"]} for r in ranked}
        return None
```

New helpers (below `_embed_scores`):

```python
    def _rel(self, scores: dict, s) -> float:
        e = scores.get(self._sid(s))
        return e["score"] if e else 0.0

    def _vec(self, scores: dict, s) -> list:
        e = scores.get(self._sid(s))
        return e["vec"] if e else []

    def _mmr_body(self, bodies: list, scores: dict) -> list:
        """Greedy maximal-marginal-relevance pick of the body snippets: the first pick
        is pure relevance; each next pick maximises relevance minus its worst cosine
        overlap with what is already picked. Kills the near-duplicate top-3 that made
        letters retell one experience three times. Native language stays the tiebreak."""
        pool = list(bodies)
        picked: list = []
        while pool and len(picked) < self.max_body:

            def mmr(s):
                overlap = max(
                    (
                        SnippetEmbed._cos(self._vec(scores, s), self._vec(scores, p))
                        for p in picked
                    ),
                    default=0.0,
                )
                return (
                    self._MMR_LAMBDA * self._rel(scores, s)
                    - (1 - self._MMR_LAMBDA) * overlap
                )

            best = max(pool, key=lambda s: (mmr(s), self._native(s)))
            picked.append(best)
            pool.remove(best)
        return picked
```

`select()` — the embed branch changes (structural branch untouched):

```python
        scores = self._embed_scores(active)
        if scores is not None:
            ranking = "embedding"
            key = lambda s: (self._rel(scores, s), self._native(s))
            body = self._mmr_body(bodies, scores)
        else:
```

Also update the class docstring's embed sentence:

```
    ... The embed path has no gate: the ranking is the gate — and the body pick is
    MMR-diversified (relevance minus overlap with what's already picked), so the
    "best three" are three different stories, not one story three times.
```

**2c. `CoverLetter`** — critique + generalized repair. New class attrs next to
`_REWRITE_TAX`:

```python
    # Prose-quality critique runs on the grades whose writers actually reshape text;
    # light glue is exempt (a 1B can't act on critique — and glue is the point there).
    _CRITIC_GRADES = ("standard", "strong")
    # A standard "polish" that lost >40% of the snippets' words is a summary, not a
    # polish — deterministic backstop, works even when the critic model is down.
    _MIN_BODY_RATIO = 0.6
    _SHRINKAGE_NOTE = (
        "the body summarizes the snippets instead of writing them out — rewrite at "
        "full length, one paragraph per theme, keeping every concrete claim"
    )
```

`build()` is restructured — the personal paragraph moves to the FRONT (it opens the
letter and the writer needs it as context), the critique/repair flow lands after the
audit, and `ai_share` collapses to a single computation. Full new body:

```python
    def build(self) -> dict:
        language = (getattr(self.job_posting, "language", "") or "en").lower()[:2]
        title = getattr(self.job_posting, "title", "") or ""
        sel = SnippetSelector(
            self.cv,
            self.user.pk,
            max_body=self.max_body_snippets,
            posting_language=language,
            posting_text=self._posting_text(),
            user=self.user,
            alias=self.alias,
            embed_alias=self.embed_alias,
        ).select()

        # Personal paragraph FIRST (letter-quality decision, 2026-07-12): it opens the
        # letter, so the writer gets it as context and can arc back to it. Only a real
        # paragraph enters a prompt — the stub is a UI artifact, not writer input.
        pp = self._personal_paragraph(language, title)
        opening = "" if pp["is_stub"] else pp["text"]

        woven = CoverLetterWriter(
            sel["ordered"],
            candidate_name=self._candidate_name(),
            title=title,
            language=language,
            grade=self.grade,
            alias=self.alias,
            user=self.user,
            posting_text=self._posting_text(),
            opening_paragraph=opening,
        ).write()
        # The writer returns '' when the LLM failed OR there were no snippets to weave. Either
        # way fall back to the raw stitched snippets (no slop), and remember it for _grounding.
        weave_failed = not woven
        body = woven or "\n\n".join(s.content for s in sel["ordered"])
        body_is_ai_fallback = not sel["ordered"]

        # Strong composes freely (and sees the posting), so its audit is not optional.
        # The critic reviews prose quality on standard+strong; audit claims and critic
        # notes then share ONE repair rewrite (strong re-audits the rewrite; the
        # advisory critique is not re-run).
        verify = self.verify_grounding or self.grade == "strong"
        grounding = self._grounding(body, sel["ordered"], weave_failed, verify)
        critique = self._critique(body, sel["ordered"], weave_failed)
        body, grounding, critique = self._repair(
            body, sel["ordered"], grounding, critique, language, title, verify, opening
        )

        result = {
            "language": language,
            "subject": self._subject(language, title),
            "salutation": self._salutation(language),
            "body": body,
            "sender": self._sender(),
            "recipient": self._recipient(),
            "date": timezone.localdate().isoformat(),
            "closing": _CLOSING.get(language, _CLOSING["en"]),
            "snippets_used": [f"{s.kind}:{s.pk}" for s in sel["ordered"]],
            "ai_share": self._ai_share(
                sel["ordered"],
                language,
                body_is_ai_fallback,
                personal_words=0 if pp["is_stub"] else len(pp["text"].split()),
            ),
            "snippet_provenance": {
                "native": [
                    f"{s.kind}:{s.pk}" for s in sel["ordered"] if s.language == language
                ],
                "translated": [
                    f"{s.kind}:{s.pk}" for s in sel["ordered"] if s.language != language
                ],
            },
            "grounding": grounding,
            "critique": critique,
            "snippet_ranking": sel["ranking"],
            "personal_paragraph": pp["text"],
            "personal_paragraph_is_stub": pp["is_stub"],
            "personal_paragraph_sources": pp["sources"],
            "personal_paragraph_grounding": pp["grounding"],
        }

        result["text"] = self.render_markdown(result)
        return result
```

Replace `_strong_repair` entirely with:

```python
    def _critique(self, body, snippets, weave_failed) -> dict:
        """Advisory prose-quality review: {'count': int | None, 'claims': [str]}.

        Runs only on _CRITIC_GRADES with a real woven body (the raw-fallback body IS
        the snippets — nothing to review). The standard grade adds a deterministic
        shrinkage backstop: a polish that lost >40% of the snippet words is
        summarising, and that finding must not depend on the critic model being up."""
        if self.grade not in self._CRITIC_GRADES or weave_failed or not snippets:
            return {"count": None, "claims": []}
        critique = LetterCritic(
            body, snippets, alias=self.verifier_alias or self.alias, user=self.user
        ).critique()
        if self.grade == "standard" and self._shrunk(body, snippets):
            claims = critique["claims"] + [self._SHRINKAGE_NOTE]
            critique = {"count": len(claims), "claims": claims}
        return critique

    def _shrunk(self, body, snippets) -> bool:
        snippet_words = sum(len(s.content.split()) for s in snippets)
        return snippet_words > 0 and len(body.split()) < (
            self._MIN_BODY_RATIO * snippet_words
        )

    def _repair(
        self, body, snippets, grounding, critique, language, title, verify, opening
    ):
        """ONE combined repair pass over draft one, never a loop: strong's unsupported
        claims and any critique notes ride the same rewrite. Afterwards the grounding
        is re-audited when auditing is on (safety stays honest about the shipped body);
        the critique is NOT re-run — advisory — its `repaired` flag means "the flagged
        draft was replaced", set only when the critique itself contributed notes.
        `grounding.repaired` keeps its v2 contract on strong: True only when a rewrite
        actually replaced the body. `opening` travels along so the rewrite keeps the
        same arc context as draft one."""
        strong = self.grade == "strong"
        claims = (grounding.get("claims") or []) if strong else []
        notes = critique.get("claims") or []
        if not claims and not notes:
            if strong:
                return body, {**grounding, "repaired": False}, critique
            return body, grounding, critique
        rewritten = CoverLetterWriter(
            snippets,
            candidate_name=self._candidate_name(),
            title=title,
            language=language,
            grade=self.grade,
            alias=self.alias,
            user=self.user,
            posting_text=self._posting_text(),
            unsupported_claims=claims,
            revision_notes=notes,
            opening_paragraph=opening,
        ).write()
        if not rewritten:
            out_g = {**grounding, "repaired": False} if strong else grounding
            out_c = {**critique, "repaired": False} if notes else critique
            return body, out_g, out_c
        new_g = self._grounding(rewritten, snippets, weave_failed=False, verify=verify)
        if strong:
            new_g = {**new_g, "repaired": True}
        out_c = {**critique, "repaired": True} if notes else critique
        return rewritten, new_g, out_c
```

> Subtleties worth knowing while typing:
> - Standard's opt-in grounding stays **flag-only**: `claims` is forced empty below
>   strong, so dirty grounding alone never triggers a standard repair (v2 contract).
>   But when a critique-triggered rewrite happens *and* `verify_grounding` is on, the
>   re-audit runs so the reported grounding describes the shipped body, not draft one.
> - `verify=False` makes `_grounding(...)` return the `{None, []}` sentinel without an
>   LLM call, which equals the untouched dict — no special-casing needed.
> - Docstring of `build()`/module header: update the pipeline-v2 header paragraph to
>   mention the critic ("…compensated by an always-on grounding audit **plus a prose
>   critic whose findings share the single repair pass**") and the paragraph-as-opener.

**2d. assembly order flips** — `editable_body()` (module level) becomes:

```python
def editable_body(letter: dict) -> str:
    """The sendable middle of a built letter: personal paragraph (real or stub) first,
    then the body — the paragraph OPENS the letter (letter-quality decision,
    2026-07-12); subject/salutation/date/closing/addresses live in `letter_meta` and
    are re-assembled at render/export time.
    """
    parts = [letter.get("personal_paragraph") or "", letter.get("body", "")]
    return "\n\n".join(p for p in parts if p)
```

and in `render_markdown()`, the personal-paragraph block moves ABOVE the body:

```python
        out.append(r["salutation"])
        out.append("")
        if r.get("personal_paragraph"):
            out.append(r["personal_paragraph"])
            out.append("")
        out.append(r["body"])
        out.append("")
        out.append(_CLOSING.get(r["language"], _CLOSING["en"]))
```

### 3. `frontend/src/lib/queries/generations.ts`

After the `Grounding` type:

```ts
export type Critique = {
  count: number | null;
  claims: string[];
  /** Standard+ only: a quality repair replaced the body (true) or the rewrite failed (false). */
  repaired?: boolean;
};
```

`CoverLetterResult` gains (next to `grounding`):

```ts
  /** Prose-quality critique (standard+strong): advisory, feeds the repair pass. */
  critique?: Critique;
```

After `groundingBadge`:

```ts
export function qualityBadge(c: Critique | undefined): Badge | null {
  // Advisory rung: when the critic didn't run (light grade, old runs, critic down)
  // there is nothing to say — no badge, unlike grounding's explicit "not checked".
  if (!c || c.count === null) return null;
  const suffix = c.repaired ? " · repaired" : "";
  if (c.count === 0) return { tone: "green", label: "quality ok" };
  return {
    tone: "amber",
    label: `${c.count} issue${c.count === 1 ? "" : "s"}${suffix}`,
  };
}
```

### 3b. `frontend/src/lib/letter-doc.ts`

`editableBody()` flips to mirror the backend (comment included — the mirror claim is
the contract):

```ts
/** Mirror of backend jac/cover_letter.py editable_body(): the personal paragraph
 *  (real or stub) OPENS the letter, then the body. */
export function editableBody(letter: CoverLetterResult): string {
  const parts = [letter.personal_paragraph, letter.body];
  return parts.filter(Boolean).join("\n\n");
}
```

### 4. `frontend/src/components/applications/generate-panel.tsx`

Import `qualityBadge` from `@/lib/queries/generations`. Next to the existing badge
computations:

```ts
  const quality = result ? qualityBadge(result.cover_letter.critique) : null;
```

and render it right after the grounding `<span>` (same pattern, tooltip = the issues):

```tsx
            {quality && (
              <span
                className={`rounded px-2 py-0.5 text-xs ${toneClass(quality.tone)}`}
                title={(result.cover_letter.critique?.claims ?? []).join("\n")}
              >
                {quality.label}
              </span>
            )}
```

## tests (already on disk, land red)

| file                                       | class / block                        | covers                                                                                                                                                          |
| ------------------------------------------ | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/jac/tests/test_llm_rungs.py`      | `LetterCriticTests` (new)            | prompt carries snippets/body/no-fact-check scope; ISSUES parse (n, 0, listed-lines-win, garbage→None); LLM error → None, muted                                    |
| `backend/jac/tests/test_llm_rungs.py`      | `CoverLetterWriterPromptTests` (ext) | `revision_notes` block renders / absent by default / coexists with `unsupported_claims`; `opening_paragraph` block renders with "do not repeat" / absent by default; standard clause pins "not a summary"; strong pins "at most once"; both pin "call to action" |
| `backend/jac/tests/test_llm_rungs.py`      | `PersonalParagraphWriterTests` / `ParagraphGroundingCheckTests` (ext) | writer prompt pins the opener role + "professional strength" framing; check prompt pins "reframing is not fabrication" |
| `backend/jac/tests/test_cover_letter.py`   | `SnippetSelectorMMRTests` (new)      | near-duplicate #2 loses its slot to the distinct #3; first pick stays pure relevance; ranking still "embedding"                                                   |
| `backend/jac/tests/test_cover_letter.py`   | `CoverLetterCritiqueTests` (new)     | light never critiques; standard clean/dirty/repair-prompt-carries-notes; shrinkage backstop (clean critic + tiny body → repair); critic failure skips repair; empty rewrite keeps draft 1 |
| `backend/jac/tests/test_cover_letter.py`   | existing classes (updated)           | flows re-pinned to the new call order (**paragraph→writer→audit→critic→repair→re-audit**) with new `side_effect` lists + call counts; the paragraph opens `text`; the real opener reaches the writer prompt, the stub never does; `editable_body` order flipped |
| `frontend/tests/lib/generations.test.ts`   | `qualityBadge` (ext)                 | null when unchecked/absent; green clean; amber count + `· repaired` suffix; failed repair no suffix                                                               |
| `frontend/tests/lib/letter-doc.test.ts`    | `editableBody` (updated)             | paragraph (and stub) precede the body                                                                                                                              |
| `frontend/tests/lib/applications.test.ts`  | `runToApplicationPatch` (updated)    | the applied `cover_letter` string flows through `editableBody` — expectation flipped to opener order (missed in the original map, caught by the full-suite run)   |

Red-state verified 2026-07-12 (after the paragraph-as-opener extension): backend 37
red (21 failures + 16 errors), all in the new/re-pinned tests, zero collateral;
frontend 6 red / 40 green across `generations.test.ts` (4 — `qualityBadge` missing)
and `letter-doc.test.ts` (2 — order flip). `test_stub_is_never_fed_to_the_writer`
starts green by design — it guards a property the current code already has.

**Implemented 2026-07-12 (volatile, Claude).** Green-state: full backend suite
547 OK, full frontend suite 242/242, `npx tsc -b` clean. Remaining work = the live
verification below (Lukas).

Run:

```
cd backend && python manage.py test jac.tests.test_cover_letter jac.tests.test_llm_rungs
cd frontend && npx vitest run tests/lib/generations.test.ts tests/lib/letter-doc.test.ts
```

## Verification (Lukas)

1. Both suites green, `npx tsc -b` clean, full backend suite green.
2. Live, dev stack up (valkey + ollama + runserver + celery worker):
   - **standard** (ollama): the letter should now be several paragraphs approximating
     the snippets' combined length — not a one-paragraph summary. Watch the run's
     request logs: writer + critic (+ repair when flagged).
   - **strong** (commercial alias): read for redundancy — the same experience should
     appear once. Badges: grounding as before, plus the new quality badge
     ("quality ok" / "n issues · repaired").
   - `snippets: embedding` badge still shows; with ≥4 active body snippets covering
     overlapping stories, check the chosen 3 actually differ in topic (MMR working).
3. **Personal paragraph, live** (web-capable alias + questionnaire filled): the
   paragraph now OPENS the letter; the body's last paragraph carries a call to action
   + thanks and — when the opener is real — one clause echoing its theme. Read the
   paragraph as a hiring manager: traits should land as strengths ("works
   independently, delivers without hand-holding"), never as liabilities ("values
   their independence, speaks their mind"). Editor/export: the paragraph (or the loud
   stub) sits at the top of the body textarea and the rendered PDF.
4. `cover_letter` management command smoke run over the corpus still passes.
5. Judgement call for Results: is 0.7/0.3 the right MMR balance, is the 0.6 shrinkage
   floor right for your snippet lengths, and does the corporate reframing stay honest
   enough for your taste?

## Results

_(filled by Lukas after testing — raw test output, observed issues, what works)_
