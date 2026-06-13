# Phase 4b-bis — Iterative breadth-controlled distill loop

> Setup guide, authored before the code. Second of the two Phase-4b slices; it
> builds directly on [phase-4b-setup-guide.md](phase-4b-setup-guide.md) (the
> SLM-robustness slice) and must not start until that one is committed and green.
> Follow top to bottom; every step ends with a **Verify**.

## 1. Goal

By the end, the "light" (and "standard") fallback ladder no longer does a single-shot
keyword filter — it runs a **count-targeted loop**: distill the posting to a keyword
profile, filter the CV, and if too few entries survive **re-distill broader**; if too
many, **re-distill stricter** — converging on a target band (default 12–22 total
entries) within a bounded number of rounds. This is the user's idea ("distill → filter
→ too few? broaden; too many? narrow") and it keeps the local model viable because
every round's LLM *output* is a small keyword list, never per-entry scoring.

Acceptance: for `data/test_job.md` on the `ollama` alias, the final selection lands
inside the band, and the logs show breadth adjusting across rounds.

This slice does **not** touch the cover letter (4c), German output (4d), or the
`strong` ladder's conversational/scoring tiers.

## 2. Preflight

- **4b committed + green.** `git log --oneline -1` shows `Phase 4b: SLM-robust CV
  pipeline …`; `python manage.py test` → `Ran N tests … OK` (record N: ____).
- **The light ladder works.** `python manage.py cv_test --user 1 --job-file
  data/test_job.md` → the `ollama` pass returns a non-empty CV via the `keyword` tier.
  That single-shot keyword tier is what this slice upgrades.

## 3. The contract you're coding against

- **`extract_job_keywords(job_text, llm, user)`** in
  [backend/jac/llm.py](../../backend/jac/llm.py#L29) — returns a same-language keyword
  list. We add a `breadth` param.
- **`CV.deterministic_filter(keywords_or_text, …)`** in
  [backend/jac/cv.py:170](../../backend/jac/cv.py#L170) — passing a **list** filters
  once with no loose/strict retry (we control breadth ourselves, so we pass the list).
- **`CV._MIN_PER_SECTION`** ([cv.py:39](../../backend/jac/cv.py#L39)) — the per-section
  floors stay authoritative; the loop targets a *total* band on top of them.
- **The ladder** from 4b — `ai_tailor_with_fallback` selects `light`/`standard`/`strong`
  ladders. We slot the new loop in as the cheap rung.

> Non-obvious choice: the loop restores a fresh snapshot **each round** before
> filtering, so round N's breadth change is applied to the full CV, not to round N-1's
> already-narrowed set (otherwise broadening could never recover dropped entries).

## 4. Stack additions

None.

## 5. The changes, in order

### 5a. Breadth dial on keyword extraction — [backend/jac/llm.py](../../backend/jac/llm.py)

```python
_BREADTH_GUIDANCE = {
    "narrow": (
        "Return 12–20 keywords. Favour the core must-have hard skills, tools, "
        "and the exact role title. Omit generic soft-skills, benefits, and filler."
    ),
    "balanced": (
        "Return 25–40 keywords. Cover hard skills, tools, methods, role concepts, "
        "and the main domain/industry terms."
    ),
    "broad": (
        "Return 40–60 keywords. Everything in 'balanced' PLUS adjacent and "
        "transferable terms, broader skill categories, and related role families "
        "(e.g. for a data-center field tech also include Server, Hardware, Netzwerk, "
        "Linux, IT-Support)."
    ),
}


def extract_job_keywords(
    job_text: str, llm: str = "default", user=None, breadth: str = "balanced"
) -> list[str]:
    """Free-form keyword extraction from a job posting. SLM-friendly.

    Keywords are returned in the SAME LANGUAGE as the posting so they can be used
    directly as substring needles against a CV. `breadth` ('narrow'|'balanced'|'broad')
    tunes how many and how transferable the keywords are — the distill loop sweeps it.
    """
    count_rule = _BREADTH_GUIDANCE.get(breadth, _BREADTH_GUIDANCE["balanced"])
    system = (
        "You extract a set of keywords from a job posting for substring matching "
        "against a CV. Return ONE JSON array of short strings.\n"
        "\n"
        "RULES:\n"
        "1. LANGUAGE: Return keywords in the SAME LANGUAGE as the posting. Do NOT "
        "translate. Preserve the surface form exactly — the downstream consumer does "
        "substring matching, so 'Serveradministration' and 'server administration' "
        "are NOT interchangeable.\n"
        f"2. COUNT/BREADTH: {count_rule}\n"
        "3. Single words and short phrases (2–3 words max). No duplicates, no "
        "stopwords, no sentences, no prose, no markdown.\n"
        "\n"
        "Output: a single JSON array of strings. Nothing else."
    )
    raw = complete(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": job_text},
        ],
        alias=llm,
        user=user,
    )
    parsed = _parse_json(raw, context="extract_job_keywords")
    if not isinstance(parsed, list):
        raise ValueError(f"extract_job_keywords: expected list, got {type(parsed)}")
    return [str(k) for k in parsed]
```

(This shortens the old, very long system prompt — the category coverage now lives in the
breadth tiers. Keep the old wording if you prefer; only the `breadth`/`count_rule`
wiring is required.)

**Verify:**
```bash
python manage.py shell -c "
from jac.cv import CV
cv = CV(user_pk=1)
import jac.llm as L
for b in ('narrow','balanced','broad'):
    print(b, len(L.extract_job_keywords(open('data/test_job.md').read(), llm='ollama', breadth=b)))
"
```
→ counts rise narrow → balanced → broad.

### 5b. The loop — [backend/jac/cv.py](../../backend/jac/cv.py) (new method on `CV`)

```python
    def ai_distill_and_filter(
        self,
        job_text: str,
        llm: str = "default",
        target_min: int = 12,
        target_max: int = 22,
        max_rounds: int = 3,
        language: str | None = None,
    ) -> dict:
        """Distill the posting to keywords, filter, and adjust breadth by the
        survivor count: too few -> broaden the next distill; too many -> narrow it.

        SLM-friendly: every round's LLM output is a small keyword list, never the
        per-entry scoring that times out local models. Restores a fresh snapshot
        each round so broadening can recover entries an earlier round dropped.
        Mutates self.entries to the final round's selection. Returns
        {"keywords", "breadth", "rounds"}.
        """
        snapshot = {k: list(v) for k, v in self.entries.items()}
        order = ["narrow", "balanced", "broad"]
        breadth = "balanced"
        keywords: list[str] = []
        rounds = 0

        for rounds in range(1, max_rounds + 1):
            self.entries = {k: list(v) for k, v in snapshot.items()}
            keywords = jac_llm.extract_job_keywords(
                job_text, llm=llm, user=self.user, breadth=breadth
            )
            self.deterministic_filter(keywords)  # list -> single pass, no retry
            total = sum(len(v) for v in self.entries.values())
            logger.debug(
                "ai_distill_and_filter: round=%d breadth=%s -> %d entries",
                rounds, breadth, total,
            )
            if total < target_min and breadth != "broad":
                breadth = order[order.index(breadth) + 1]   # broaden
                continue
            if total > target_max and breadth != "narrow":
                breadth = order[order.index(breadth) - 1]    # narrow
                continue
            break  # in band, or already at an extreme — accept this round

        return {"keywords": keywords, "breadth": breadth, "rounds": rounds}
```

`language` is accepted for signature parity with the other tiers (the keyword needles
are already same-language, so it's unused here — keep it for the tier wrapper below).

### 5c. Slot it into the ladder — [backend/jac/cv.py](../../backend/jac/cv.py) `ai_tailor_with_fallback`

Add a `tier_distill` closure beside the others and swap it in:

```python
        def tier_distill():
            try:
                meta = self.ai_distill_and_filter(job_text, llm=llm, language=language)
                if any(self.entries.values()):
                    return {"tier": "distill", "selection": None, "keywords": meta["keywords"]}
            except Exception:
                logger.warning("ai_tailor_with_fallback: distill failed", exc_info=True)
            restore()
            return None

        ladders = {
            "strong":   [tier_conversational, tier_filter, tier_keyword, tier_deterministic],
            "standard": [tier_filter, tier_distill, tier_deterministic],
            "light":    [tier_distill, tier_deterministic],
        }
```

Update the method docstring's tier list (`keyword` → `distill` for light/standard) and
note that `"tier"` can now be `"distill"`. `cv_test`/`cv_eval` just print the tier, so
no consumer change is needed.

> Keep `tier_keyword` defined — `strong` still uses it as a late rung, and it's the
> fallback if you ever want a non-looping light ladder.

## 6. Per-step Verify blocks

- After 5a: breadth sweep shows rising keyword counts.
- After 5b: `python manage.py shell -c "from jac.cv import CV; cv=CV(user_pk=1);
  print(cv.ai_distill_and_filter(open('data/test_job.md').read(), llm='ollama'))"` →
  prints `{'keywords': [...], 'breadth': ..., 'rounds': N}` and `cv.entries` total sits
  near the 12–22 band.
- After 5c: `ai_tailor_with_fallback(..., llm='ollama')` returns `tier == "distill"`.

## 7. End-to-end verification — the full loop

```bash
python manage.py cv_test --user 1 --job-file data/test_job.md
```
1. The `ollama`/`default` passes now report the **distill** path (watch the `[cv]`
   debug lines: round/breadth/count, adjusting toward the band).
2. The final per-section counts are sensible — not 3 entries, not the whole CV.
3. Try a deliberately broad posting and a deliberately narrow one (or tweak
   `target_min`/`target_max` via a quick shell call) to see the loop broaden vs narrow.
4. `opeani` (strong) is unchanged — conversational-first.

## 8. What you should have at the end

```
backend/jac/llm.py    # extract_job_keywords gains breadth + _BREADTH_GUIDANCE
backend/jac/cv.py     # CV.ai_distill_and_filter + tier_distill wired into light/standard
```

Add tests (bump N): mock `jac_llm.extract_job_keywords` to return a *large* set first
(→ many survivors → loop narrows) and a *tiny* set (→ few survivors → loop broadens),
assert the final `breadth`/`rounds` and that `total` moves toward the band; assert
`ai_tailor_with_fallback` with `strength="light"` returns `tier == "distill"`. Re-run
`python manage.py test` (green), then commit code + this guide:

```
Phase 4b-bis: iterative breadth-controlled distill loop
```

## 9. Known gaps to revisit

- **Per-section targeting.** The loop targets a *total* band; a posting could converge
  with, say, zero projects. The `_MIN_PER_SECTION` floors only bite in the scoring
  tiers, not here. If 4b findings show lopsided sections, add per-section breadth — a
  later micro-slice, not now.
- **`builds_on`/`related_skills` closure.** Still deferred; revisit once the loop's
  selections are dogfood-reviewed.
- **Breadth granularity.** Three levels (narrow/balanced/broad) may over/undershoot on
  some postings; a finer dial or a binary-search on keyword count is a future option.

## 10. What's next

**Phase 4c — cover-letter generator** (per the roadmap): a `CoverLetter` builder in new
`backend/jac/letter.py` that stitches the user's `ResumeSnippet`s onto the tailored CV
with the same AI-escalation discipline, rendered via a `LetterRender`. The robust,
capability-tiered LLM plumbing from 4b/4b-bis is what it builds on.
```
