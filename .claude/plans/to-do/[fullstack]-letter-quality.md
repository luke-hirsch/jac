# [fullstack] letter quality — MMR snippet diversity, LetterCritic + repair, anti-summary standard

> **Mode note:** default-strict — Lukas types the non-test source from this guide.
> Tests land first (red) as acceptance criteria. Branch: `fullstack/letter-quality`
> (cut off a *dirty* main that still carries the uncommitted pipeline-v2 phase — commit
> or wrap that up before/with this work so the phases don't tangle in one commit).

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

## call order (for reading the tests' `side_effect` lists)

```
writer → grounding audit (strong always / opt-in) → critic (standard+strong)
      → [repair rewrite → grounding re-audit (iff auditing)]   # only when triggered
      → personal paragraph (unchanged, after everything)
```

Strong worst case: 5 calls (was 4). Standard default: 2 calls, 3 when the critic
triggers a repair.

## affected files

| file                                             | change                                                                             |
| ------------------------------------------------ | ---------------------------------------------------------------------------------- |
| `backend/jac/llm_prompts.py`                     | `Embed.ranked_vectors()` + `_cos` → staticmethod; `LetterCritic`; writer clauses + `revision_notes` |
| `backend/jac/cover_letter.py`                    | `SnippetSelector` MMR body pick; `CoverLetter` critique/repair flow (replaces `_strong_repair`) |
| `frontend/src/lib/queries/generations.ts`        | `Critique` type, `critique` key on `CoverLetterResult`, `qualityBadge()`            |
| `frontend/src/components/applications/generate-panel.tsx` | render the quality badge                                                  |
| `backend/jac/tests/test_llm_rungs.py`            | (AI, on disk) `LetterCriticTests`, writer prompt extensions                        |
| `backend/jac/tests/test_cover_letter.py`         | (AI, on disk) `SnippetSelectorMMRTests`, `CoverLetterCritiqueTests`, existing classes updated to the new call order |
| `frontend/tests/lib/generations.test.ts`         | (AI, on disk) `qualityBadge` variants                                              |

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
            "never compress the substance away. Do not invent facts the snippets do not "
            "state."
        ),
        "strong": (
            "Compose an original, persuasive letter body tailored to THIS job posting. Use the "
            "posting only to choose emphasis, ordering, and tone — the posting is NEVER a "
            "source of facts about the candidate. Every factual claim (skills, employers, "
            "titles, numbers, dates, achievements) must come from the snippets — invent "
            "nothing. State each experience, project, and achievement at most once — never "
            "retell the same fact in different words."
        ),
```

**1c. `CoverLetterWriter`** — `revision_notes` kwarg (the critique repair channel,
parallel to `unsupported_claims`). `__init__` signature gains the last parameter:

```python
        posting_text: str = "",
        unsupported_claims: list[str] | None = None,
        revision_notes: list[str] | None = None,
    ):
```

body of `__init__` gains:

```python
        self.revision_notes = revision_notes or []
```

and `_prompt()` gets a `notes` block after `repair` (return line changes too):

```python
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
            f"{posting}{repair}{notes}"
            f"SNIPPETS (your only source of facts):\n{blocks}\n\nLETTER BODY:"
        )
```

Also update the class docstring's last paragraph to mention both channels:

```python
    `unsupported_claims` and `revision_notes` are the repair-pass channels: the
    grounding audit's findings and the LetterCritic's writing notes are fed back so
    one rewrite can fix both.
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

In `build()`, replace the current grounding/repair block

```python
        # Strong composes freely (and sees the posting), so its audit is not optional; the
        # repair pass gets one shot at removing whatever the audit flags.
        verify = self.verify_grounding or self.grade == "strong"
        grounding = self._grounding(body, sel["ordered"], weave_failed, verify)
        if self.grade == "strong":
            body, grounding = self._strong_repair(
                body, sel["ordered"], grounding, language, title
            )
```

with

```python
        # Strong composes freely (and sees the posting), so its audit is not optional.
        # The critic reviews prose quality on standard+strong; audit claims and critic
        # notes then share ONE repair rewrite (strong re-audits the rewrite; the
        # advisory critique is not re-run).
        verify = self.verify_grounding or self.grade == "strong"
        grounding = self._grounding(body, sel["ordered"], weave_failed, verify)
        critique = self._critique(body, sel["ordered"], weave_failed)
        body, grounding, critique = self._repair(
            body, sel["ordered"], grounding, critique, language, title, verify
        )
```

and add to the `result` dict, right after `"grounding": grounding,`:

```python
            "critique": critique,
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

    def _repair(self, body, snippets, grounding, critique, language, title, verify):
        """ONE combined repair pass over draft one, never a loop: strong's unsupported
        claims and any critique notes ride the same rewrite. Afterwards the grounding
        is re-audited when auditing is on (safety stays honest about the shipped body);
        the critique is NOT re-run — advisory — its `repaired` flag means "the flagged
        draft was replaced", set only when the critique itself contributed notes.
        `grounding.repaired` keeps its v2 contract on strong: True only when a rewrite
        actually replaced the body."""
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
>   critic whose findings share the single repair pass**").

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
| `backend/jac/tests/test_llm_rungs.py`      | `CoverLetterWriterPromptTests` (ext) | `revision_notes` block renders / absent by default / coexists with `unsupported_claims`; standard clause pins "not a summary"; strong clause pins "at most once" |
| `backend/jac/tests/test_cover_letter.py`   | `SnippetSelectorMMRTests` (new)      | near-duplicate #2 loses its slot to the distinct #3; first pick stays pure relevance; ranking still "embedding"                                                   |
| `backend/jac/tests/test_cover_letter.py`   | `CoverLetterCritiqueTests` (new)     | light never critiques; standard clean/dirty/repair-prompt-carries-notes; shrinkage backstop (clean critic + tiny body → repair); critic failure skips repair; empty rewrite keeps draft 1 |
| `backend/jac/tests/test_cover_letter.py`   | existing classes (updated)           | strong flow re-pinned to the new call order (writer→audit→critic→repair→re-audit) with new `side_effect` lists + call counts; standard opt-in now writer+critic; personal-paragraph orderings updated |
| `frontend/tests/lib/generations.test.ts`   | `qualityBadge` (ext)                 | null when unchecked/absent; green clean; amber count + `· repaired` suffix; failed repair no suffix                                                               |

Red-state verified 2026-07-12: backend 29 red (all in the new/re-pinned tests, zero
collateral), frontend 4 red / 19 green in `generations.test.ts` (`qualityBadge is not
a function` until section 3 is typed).

Run:

```
cd backend && python manage.py test jac.tests.test_cover_letter jac.tests.test_llm_rungs
cd frontend && npx vitest run tests/lib/generations.test.ts
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
3. `cover_letter` management command smoke run over the corpus still passes.
4. Judgement call for Results: is 0.7/0.3 the right MMR balance, and is the 0.6
   shrinkage floor right for your snippet lengths?

## Results

_(filled by Lukas after testing — raw test output, observed issues, what works)_
