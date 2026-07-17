# [backend] pipeline-single-executor

> **Rework guide 2 of 3** — *single-executor redesign (2026-07-16)*. Depends on guide 1
> (`executor-connector`); guide 3 (`entry-pins`) rides the hooks this guide types. Same branch
> (`backend/executor-rework`). Supersedes the pipeline half of the old
> `[backend]-selection-ladder-remap`.

## Context / goal

Rewire the whole jac pipeline onto the invariant: **a generation run touches exactly one
executor** (`llm_connector.executor.Executor`). Concretely:

- **Modes renamed** to the user-facing vocabulary: `manual` / **`standard`** (was instruct) /
  **`high`** (was conversational). `high` is **commercial-only**; a HirschAI run is always
  `standard`.
- **HirschAI run**: embedding (ranking floor + fallback) → instruct selection → writer →
  **always-on** proofread (critic) + fact-check (faithfulness audit) — all on the tower.
  Personal paragraph always the loud stub (web research is a commercial capability).
- **Commercial run**: NO embedding anywhere (nothing touches the tower — the privacy promise);
  instruct/conversational selection directly over all entries, writer + audits on the same
  provider; personal paragraph real iff the executor can web-search AND a personality dossier
  exists.
- **Auto-run on application create** (never retroactive): creating an application enqueues a
  `standard` run on `default_executor(user)` when one exists; no executor → the manual flow.
- **`GenerationRun` goes on a diet**: `provider` + `model` + `mode` replace
  `alias`/`verifier_alias`/`research_alias`/`verify_grounding`/`personal_paragraph`/
  `max_body_snippets`/`evaluation`/`score`.
- The deprecated surfaces die for real: `find_address` (address web search), the chat strength
  gate, the alias plumbing in every rung.

## Affected files

| Path | Change |
| --- | --- |
| `jac/models.py` | `Mode` → manual/standard/high; `normalize_mode` → standard; `GenerationRun` reshaped. |
| `jac/filter.py` | `CVFilter(mode, executor, pinned)`; ladder per executor; `GenerationError` (loud commercial failure). |
| `jac/cv.py` | `filter_cv(job_post_text, mode, executor, pinned)`; the broken `FILTER_GRADE`/`filter_grade` remnants die; `apply_selection` stamps `pinned`/`warning` too. |
| `jac/llm_prompts.py` | Every rung takes `executor` (Embed: `user` only); `PREFERRED_PIN` attrs die; writer clauses rekeyed standard/high. |
| `jac/cover_letter.py` | Single executor; audits always on; critic on both AI modes; paragraph purely capability-driven; `_REWRITE_TAX` rekeyed. |
| `jac/research.py` | `CompanyResearcher(..., executor=…)` — uses `executor.web_search`. |
| `jac/vectors.py` | Alias params die; collection follows the HirschAI embed model; `sync_alias` deleted. |
| `jac/signals.py` | Privacy gate: no background tower-embedding for commercial-default users. |
| `jac/tasks.py` | `generate_run` builds one `Executor`; result meta `{mode, provider, model}`; `GenerationError` fails loudly. |
| `jac/serializers.py` | Create takes `mode`/`provider`/`model` (validated via `resolve_executor`); read shapes expose them. |
| `jac/views.py` | Auto-run in `JobApplicationViewSet.perform_create`; `chat`/`rewrite` executor-keyed; `find_address` + `AddressSearch` import deleted; chat strength gate deleted. |
| `spa/models.py` | `PersonalityProfile.ensure_dossier(*, executor)`; spa-internal callers use `default_executor(user)` and skip when None. |
| `backend/*/migrations/` | Fresh initial migrations for `llm_connector`, `jac`, `spa` at the end (`makemigrations` + `migrate`, new dev DB). |

## Approach / key decisions

- **Mode names the strategy; the executor names the machine.** The DB/API values are the
  user-facing words (`standard`/`high`) — fresh DB, no legacy to translate. The prompt classes
  keep their descriptive names (`Instruct`, `Conversational`): they name *strategies*, and
  `Conversational` is simply the strategy `high` runs.
- **`high` on HirschAI is rejected at the API** (serializer 400), not silently degraded — the
  tower's 1B can't drive holistic selection, and pretending otherwise poisons trust in the mode.
  Revisit when the tower-inference-server guide lands a bigger model.
- **Commercial selection failure is loud.** On the tower, a failed instruct parse degrades to
  the embedding floor. A commercial run has no embedder by design, so: one retry, then
  `GenerationError` → the run fails with a clear message. Never silently keep-all on a paid run.
- **Audits are always on** (`proofread` = `LetterCritic`, `fact check` = `FaithfulnessCheck`),
  on the run's executor. The `verify_grounding` opt-in dies; the `count=None` ≠ `0` honesty rule
  and the single shared repair pass stay exactly as they are.
- **The personal paragraph is purely capability-driven**: real iff
  `executor.supports_web_search` (commercial) AND a personality dossier exists; else the loud
  `PERSONAL_STUB`. Consequence Lukas accepted: a HirschAI run always stubs — merely owning a
  commercial config changes nothing until a run executes there. The stub (not silence) is
  deliberate: it keeps the send-time export blocker armed and tells the user to write one.
- **Embedding ingest gets a privacy gate.** Background `sync_user_vectors` (signals) would ship
  a commercial-default user's entries to the tower behind their back. The signal now skips users
  whose default executor is commercial; if such a user later runs HirschAI explicitly, the
  query-time `reconcile` embeds on demand — that run *is* consent to tower use.
- **Auto-run lives in the view, not a signal** (`perform_create`) — it needs the request user,
  the enqueue helper, and must never fire on updates/imports. Auto-fill of an empty application
  by a finished run already exists in the task and is untouched.
- **`max_body_snippets` becomes a pipeline constant** (3) instead of a run field; the CV-scoping
  fields (`domains`/`started`/`ended`/`min_skill_proficiency`) survive unchanged.

## The code

### 1. `jac/models.py` — vocabulary + run

```python
class Mode(models.TextChoices):
    """How a generation selects/writes — the strategy axis; the run's executor
    (provider + model) is the machine axis. `standard` = instruct-labelled
    selection + polish-licence writer, every executor. `high` = holistic
    conversational selection + compose-licence writer (sees the posting, always
    audited) — commercial-only; the serializer rejects it on HirschAI. `manual`
    never enters the pipeline (the user hand-curates; CVFilter's manual branch
    keeps even a buggy caller at zero LLM/embed calls). THIS is the canonical
    list — nothing else hardcodes it."""

    manual = "manual", _("No AI")
    standard = "standard", _("Standard")
    high = "high", _("High")


def normalize_mode(value: str | None) -> str:
    """Coerce any input to a valid `Mode`. Blank/unknown/legacy -> `standard`
    (the AI default; the SPA offers `manual` itself when nothing is reachable)."""
    return str(value) if value in Mode.values else Mode.standard
```

`GenerationRun` — replace the option block (keep lifecycle + CV scoping + `Meta` +
`user`/`posting` properties):

```python
    job_application = models.ForeignKey(
        JobApplication, on_delete=models.CASCADE, related_name="runs"
    )
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.standard)
    # The executor, as loose strings (runs are history — no FK a deleted config
    # could cascade into). provider "ollama" = HirschAI; model blank = the
    # executor's own default (tower row model / catalog default).
    provider = models.CharField(max_length=32, default="ollama")
    model = models.CharField(max_length=100, blank=True)

    # CV scoping (all optional; map onto CV.__init__).
    domains = models.JSONField(default=list, blank=True)
    started = models.DateField(null=True, blank=True)
    ended = models.DateField(null=True, blank=True)
    min_skill_proficiency = models.CharField(max_length=12, blank=True)
```

Deleted fields: `alias`, `verify_grounding`, `verifier_alias`, `personal_paragraph`,
`research_alias`, `max_body_snippets`, `evaluation`, `score`.

### 2. `jac/filter.py`

```python
from llm_connector.conf import get_embed_floors
from llm_connector.executor import Executor

from jac.llm_prompts import Conversational, Embed, Instruct
from jac.models import Mode


class GenerationError(RuntimeError):
    """A selection rung failed with no safe fallback on this executor. The task
    fails the run loudly — a paid run must never silently keep everything."""
```

`__init__` + `output()` (scorers/selectors below them keep their bodies; `pinned` hooks are
typed here once — guide 3 explains and tests them):

```python
    def __init__(
        self,
        job_post_text: str,
        entries: list[dict],
        mode: str = Mode.standard,
        executor: Executor | None = None,
        pinned: set[str] | frozenset[str] | None = None,
    ):
        assert isinstance(job_post_text, str)
        self.job_post_text = job_post_text
        self.entries = entries
        self.mode = mode
        # No executor = the tower (CLI/tests). The single-executor invariant:
        # every rung below runs HERE; embedding is tower-only and only ever
        # reached when the executor IS the tower.
        self.executor = executor or Executor("ollama")
        self.pinned = frozenset(pinned or ())

    def output(self) -> dict:
        """{section: [entry dicts + score], ...}, ranked desc per section.

        The ladder, per executor:
          - manual:            keep everything unscored, ZERO llm/embed calls;
          - high:              holistic conversational selection, degrades to the
                               instruct path (same executor);
          - standard/HirschAI: instruct labels -> keep-by-verdict, degrades to the
                               embedding floor (tower-only capability);
          - standard/commercial: instruct labels; one retry, then GenerationError —
                               there is no embedding floor off the tower, and a paid
                               run must fail loudly rather than keep-all.
        """
        if self.mode == Mode.manual:
            # "No AI" is a promise: even a buggy caller must not turn it into
            # network traffic. Nothing is filtered; the human prunes.
            return self._group_all()
        if self.mode == Mode.high:
            selected = self._holistic_selection()
            if selected:
                return self._select_holistic(selected)
        attempts = 1 if self.executor.is_hirschai else 2
        for _ in range(attempts):
            labels = self._instruct_scores()
            if labels:
                return self._select_ranked(labels)
        if self.executor.is_hirschai:
            return self._select(self._embed_scores())
        raise GenerationError(
            f"selection failed on {self.executor.provider} — try again or switch executor"
        )
```

Scorer renames + signatures (bodies unchanged apart from the calls):

```python
    def _embed_scores(self) -> dict:
        ranked = Embed(self.job_post_text, self.entries, user=self.executor.user).ranked_entries()
        return {r["id"]: r["score"] for r in ranked} if ranked else {}

    def _instruct_scores(self) -> dict:
        """Instruct-LLM relevance labels {id: 0.._LABEL_MAX}. {} on failure."""
        ranked = Instruct(self.job_post_text, self.entries, executor=self.executor).ranked_entries()
        return {r["id"]: r["score"] for r in ranked} if ranked else {}

    def _holistic_selection(self) -> list[dict]:
        """Conversational holistic selection: ordered [{id, why}]. [] -> instruct fallback."""
        return Conversational(self.job_post_text, self.entries, executor=self.executor).selection()
```

The selection layer, written out in full (the pinned hooks are typed here once; guide 3 only
wires the model field + API and owns the tests). `_propagate` and the class constants are
unchanged except for one addition next to `_FAVOURITE_BONUS`:

```python
    _PIN_WARNING = (
        "pinned by you — the high-mode selection would have dropped this entry"
    )
```

`_floors()` — only the helper call changes (no arguments: there is exactly one embedder now):

```python
    def _floors(self) -> dict:
        """Per-section cosine floors: the tower row's `embed_floors` over the
        _SECTION_POLICY defaults. Floors are an embedder property; the only
        embedder is HirschAI, so no per-alias resolution remains."""
        defaults = {s: p["drop_below"] for s, p in self._SECTION_POLICY.items()}
        return {**defaults, **get_embed_floors()}
```

`_group_all` — every row now carries the `pinned` flag (manual mode + scoring-failure fallback):

```python
    def _group_all(self) -> dict:
        """Fallback when scoring fails (and the manual mode): every entry kept,
        score 0.0."""
        out: dict[str, list[dict]] = {}
        for e in self.entries:
            out.setdefault(e["type"], []).append(
                {**e, "score": 0.0, "pinned": e["id"] in self.pinned}
            )
        return out
```

`_select` (the embed-floor path). Two changes: pins survive the floor cut **inside the
keep-predicate** (so they stay in ranked position, no post-hoc append), and the `min_keep`
top-up switches from the old `keep = items[:min_keep]` replacement to `_select_ranked`'s
append-style top-up. That switch is load-bearing, not cosmetic: the old replacement would have
silently *dropped* a pinned entry ranked below `min_keep` — exactly the bug pins exist to
prevent.

```python
    def _select(self, base: dict) -> dict:
        """Apply propagation + per-section drop. Empty base -> keep everything unscored."""
        if not base:
            return self._group_all()

        eff = self._propagate(base)
        floors = self._floors()

        # Favourite nudge: small, post-propagation, so it tilts close calls without
        # lifting a ~0-scored entry over its section floor (see _FAVOURITE_BONUS).
        for e in self.entries:
            if e.get("favourite"):
                eid = e["id"]
                eff[eid] = eff.get(eid, 0.0) + self._FAVOURITE_BONUS

        by_section: dict[str, list[dict]] = {}
        for e in self.entries:
            by_section.setdefault(e["type"], []).append(e)

        out: dict[str, list[dict]] = {}
        for section, items in by_section.items():
            policy = self._SECTION_POLICY.get(
                section, {"drop_below": 0.0, "min_keep": 0}
            )
            items.sort(key=lambda e: eff.get(e["id"], 0.0), reverse=True)

            floor = floors.get(section, policy.get("drop_below", 0.0))
            min_keep = policy["min_keep"]
            if min_keep is None:
                keep = items
            else:
                # A pin survives the floor cut in its ranked position — the
                # guarantee lives inside the keep-predicate, not as an append.
                keep = [
                    e
                    for e in items
                    if eff.get(e["id"], 0.0) >= floor or e["id"] in self.pinned
                ]
                if len(keep) < min_keep:
                    # Top up from the highest-ranked remainder (pins are already
                    # in; the old `items[:min_keep]` replacement would drop a
                    # below-rank pin).
                    kept_ids = {e["id"] for e in keep}
                    for e in items:  # already score-desc sorted
                        if e["id"] not in kept_ids:
                            keep.append(e)
                            kept_ids.add(e["id"])
                            if len(keep) >= min_keep:
                                break
                    keep.sort(key=lambda e: eff.get(e["id"], 0.0), reverse=True)

            out[section] = [
                {
                    **e,
                    "score": round(eff.get(e["id"], 0.0), 4),
                    "pinned": e["id"] in self.pinned,
                }
                for e in keep
            ]
        return out
```

`_select_ranked` (the instruct-label path). The keep-verdict gains the pin clause; rows gain
the flag; everything else (stable label-desc sort, top-up, languages keep-all) is the current
body:

```python
    def _select_ranked(self, labels: dict) -> dict:
        """Selection for LLM relevance *labels* (0.._LABEL_MAX) — the instruct rung.

        Keep by the model's own verdict rather than an absolute floor, and do NOT
        propagate (the LLM already reasoned relationally from the entry text).
        Per section:
          - rank by label desc, stable (ties keep the CV's natural order);
          - keep every entry rated >= _KEEP_LABEL, plus all favourites and all
            pinned entries (user overrides);
          - guarantee min_keep by topping up from the highest-ranked remainder;
          - languages (min_keep None) keep everything.
        Kept-count therefore varies with fit (intended) — never clamped.
        """
        by_section: dict[str, list[dict]] = {}
        for e in self.entries:
            by_section.setdefault(e["type"], []).append(e)

        out: dict[str, list[dict]] = {}
        for section, items in by_section.items():
            policy = self._SECTION_POLICY.get(section, {"min_keep": 0})
            min_keep = policy["min_keep"]
            items.sort(key=lambda e: labels.get(e["id"], 0), reverse=True)

            if min_keep is None:
                keep = list(items)
            else:
                keep = [
                    e
                    for e in items
                    if labels.get(e["id"], 0) >= self._KEEP_LABEL
                    or e.get("favourite")
                    or e["id"] in self.pinned
                ]
                if len(keep) < min_keep:
                    kept_ids = {e["id"] for e in keep}
                    for e in items:  # already label-desc sorted
                        if e["id"] not in kept_ids:
                            keep.append(e)
                            kept_ids.add(e["id"])
                            if len(keep) >= min_keep:
                                break
                    keep.sort(key=lambda e: labels.get(e["id"], 0), reverse=True)

            out[section] = [
                {
                    **e,
                    "score": labels.get(e["id"], 0),
                    "pinned": e["id"] in self.pinned,
                }
                for e in keep
            ]
        return out
```

`_select_holistic` (the high-mode path). Pins join favourites in the force-back guardrail, and
this is the ONLY rung that emits a `warning`: a pinned entry the model did not choose is kept
anyway and flagged — the model may editorialise, never drop a pin. Chosen pins carry
`warning: ""`; the other rungs emit no `warning` key at all (they have no opinion).

```python
    def _select_holistic(self, selected: list[dict]) -> dict:
        """Selection for the high mode: trust the conversational model's chosen,
        ordered set and apply guardrails only — force favourites AND pins back,
        never drop languages, guarantee min_keep. No floors, no propagation, no
        count clamp (count tracks fit, by design).

        `selected` is an ordered [{id, why}]; entries absent from it are dropped,
        except as forced back by the guardrails. Surviving entries carry
        score=None (the rung emits no numeric score) and reason=<why>.
        """
        entry_by_id = {e["id"]: e for e in self.entries}
        why_by_id = {s["id"]: s["why"] for s in selected}

        by_section_all: dict[str, list[dict]] = {}
        for e in self.entries:
            by_section_all.setdefault(e["type"], []).append(e)

        # the model's chosen entries, per section, in its priority order
        chosen_by_section: dict[str, list[dict]] = {}
        for s in selected:
            e = entry_by_id.get(s["id"])
            if e is not None:
                chosen_by_section.setdefault(e["type"], []).append(e)

        out: dict[str, list[dict]] = {}
        for section, items in by_section_all.items():
            policy = self._SECTION_POLICY.get(section, {"min_keep": 0})
            min_keep = policy["min_keep"]

            keep = list(chosen_by_section.get(section, []))
            kept_ids = {e["id"] for e in keep}

            # favourites and pins are user overrides — force back any the model
            # didn't pick (pins additionally get the warning at emit time).
            for e in items:
                if (
                    e.get("favourite") or e["id"] in self.pinned
                ) and e["id"] not in kept_ids:
                    keep.append(e)
                    kept_ids.add(e["id"])

            # languages are never dropped; otherwise top up to min_keep from the
            # remainder (natural order) without re-ordering the model's picks.
            if min_keep is None:
                for e in items:
                    if e["id"] not in kept_ids:
                        keep.append(e)
                        kept_ids.add(e["id"])
            elif len(keep) < min_keep:
                for e in items:
                    if e["id"] not in kept_ids:
                        keep.append(e)
                        kept_ids.add(e["id"])
                        if len(keep) >= min_keep:
                            break

            out[section] = [
                {
                    **e,
                    "score": None,
                    "reason": why_by_id.get(e["id"], ""),
                    "pinned": e["id"] in self.pinned,
                    "warning": (
                        self._PIN_WARNING
                        if e["id"] in self.pinned and e["id"] not in why_by_id
                        else ""
                    ),
                }
                for e in keep
            ]
        return out
```

### 3. `jac/cv.py`

Imports: `from jac.models import ..., normalize_mode` (no `Grade`); `from llm_connector.executor
import Executor` (typing only). Delete `FILTER_GRADE` and the `filter_grade` parameter + branch
in `__init__` (currently a NameError — `Grade` is gone).

```python
    # filter
    def filter_cv(self, job_post_text: str, mode: str | None, executor, pinned=None):
        return CVFilter(
            job_post_text=job_post_text,
            entries=self._flatten_entries(),
            mode=normalize_mode(mode),
            executor=executor,
            pinned=pinned,
        ).output()
```

`apply_selection`: stamp the two new per-entry keys next to `relevance_score` so
`generation_result` can surface them:

```python
                obj.relevance_score = item.get("score")
                obj.pinned = bool(item.get("pinned"))
                obj.selection_warning = item.get("warning", "")
```

…and in `jac/generation_result.py` each row gains
`"pinned": getattr(obj, "pinned", False)` and
`"warning": getattr(obj, "selection_warning", "")`.

### 4. `jac/llm_prompts.py` — the executor sweep

Delete every `PREFERRED_PIN` attribute (the concept died with pins). Then mechanically per
class: replace the `user=None, alias="default"` parameter pair with `executor` (store
`self.executor = executor`), and every `complete(prompt=…, alias=self.alias, user=self.user)`
with `self.executor.complete(prompt=…)`:

| Class | New signature |
| --- | --- |
| `Embed` | `__init__(self, job_post_text, entries, user=None)` — **no executor**: embedding is tower-only; keeps `user` for vector-store scoping only. `_query()` calls the module-level `embed(inputs=…)` (no alias/user kwargs). |
| `SnippetEmbed` | inherits — nothing to do. |
| `Conversational` | `__init__(self, job_post_text, entries, *, executor)` |
| `Instruct` | `__init__(self, job_post_text, entries, *, executor)` |
| `AddressExtract` | `__init__(self, job_post_text, *, executor)` |
| `CoverLetterWriter` | `mode: str = "standard"`, `executor` replaces alias/user (rest of the kwargs unchanged) |
| `FaithfulnessCheck` | `__init__(self, body, snippets, *, executor)` |
| `LetterCritic` | `__init__(self, body, snippets, *, executor)` |
| `PersonalParagraphWriter` | `executor` replaces alias/user |
| `ParagraphGroundingCheck` | `__init__(self, paragraph, company_dossier, personality_dossier, *, executor)` |
| `LetterChat` | `executor` replaces alias/user |
| `ParagraphRewrite` | `executor` replaces alias/user |

`CoverLetterWriter` clause dict + posting visibility (docstring: two licences — `standard`
polishes, posting-blind; `high` composes and uniquely sees the posting, compensated by the
always-on audit):

```python
    _MODE_CLAUSE = {
        "standard": (  # the old instruct/polish clause, verbatim
            ...
        ),
        "high": (  # the old conversational/compose clause, verbatim
            ...
        ),
    }
    ...
        clause = self._MODE_CLAUSE.get(self.mode, self._MODE_CLAUSE["standard"])
    ...
        if self.mode == "high" and self.posting_text:
```

`Embed`'s class comment (replacing the pin comment): *"Embedding is a HirschAI-only capability:
this rung runs on the tower via `llm_connector.embed()`, and is only ever reached when the run's
executor IS the tower (CVFilter's ladder / SnippetSelector's gate). `user` scopes the vector
store, nothing else."*

### 5. `jac/cover_letter.py`

Module docstring: pipeline v4 — *"snippet selection is embedding-ranked on HirschAI runs and
structural on commercial runs (the tower must not see commercial-run data); the writer's licence
scales with mode — standard polishes, high composes (and uniquely sees the posting); proofread
(critic) + fact-check (grounding audit) always run, on the run's executor; one shared repair
pass."*

`SnippetSelector`: `alias`/`embed_alias` params → `executor`; the embed path is **gated on the
executor being the tower**:

```python
    def _embed_scores(self, active: list) -> dict | None:
        """Embedding ranking — HirschAI runs only. A commercial run must not send
        snippet text to the tower (single-executor invariant), and commercial
        executors have no embed endpoint of their own; it degrades to the
        structural scorer instead. None -> structural."""
        if not active or not self.posting_text or not self.executor.is_hirschai:
            return None
        entries = [{"id": self._sid(s), "text": s.content} for s in active]
        try:
            ranked = SnippetEmbed(
                self.posting_text, entries, user=self.executor.user
            ).ranked_vectors()
        except Exception as exc:  # noqa: BLE001 — a letter never dies on a dead embedder
            logger.info("snippet embedding unavailable: %s", exc)
            return None
        return {r["id"]: {"score": r["score"], "vec": r["vec"]} for r in ranked} if ranked else None
```

`CoverLetter.__init__` — the whole alias/pin block collapses:

```python
    # Aim for "the best three" body snippets — a pipeline constant now, not a knob.
    MAX_BODY_SNIPPETS = 3

    def __init__(self, user, job_posting, cv, *, address=None,
                 mode: str = Mode.standard, executor: Executor,
                 max_body_snippets: int | None = None):
        ...existing user/address resolution...
        self.mode = mode
        self.executor = executor
        self.max_body_snippets = max_body_snippets or self.MAX_BODY_SNIPPETS
```

(`verify_grounding`, `verifier_alias`, `personal_paragraph`, `research_alias`, `embed_alias`
parameters die.) Class constants:

```python
    _REWRITE_TAX = {"standard": 0.20, "high": 0.60}
```

(`_CRITIC_MODES` dies — both AI modes critique; the gate is just `weave_failed or not
snippets`.) In `build()`:

```python
        # Proofread + fact-check are always on (2026-07-16): every AI run ships
        # audited, on ITS executor. Audit claims (high repairs grounding) and critic
        # notes share ONE repair rewrite, as before.
        grounding = self._grounding(body, sel["ordered"], weave_failed)
        critique = self._critique(body, sel["ordered"], weave_failed)
```

- `_grounding(self, body, snippets, weave_failed)`: drop the `verify` parameter and the
  `if not verify` early-out; call `FaithfulnessCheck(body, snippets, executor=self.executor)`.
  The `weave_failed -> {"count": 0}` shortcut and the count-honesty docstring stay.
- `_critique`: gate is `if weave_failed or not snippets:`; critic call
  `LetterCritic(body, snippets, executor=self.executor)`; the shrinkage backstop keys
  `self.mode == Mode.standard` (a "polish" that shrank is a summary; `high` composes freely so
  the check is meaningless there); the one-page ceiling stays mode-blind.
- `_repair`: local `strong`/`conversational` naming → `composes = self.mode == Mode.high`; the
  re-audit call loses `verify` (always re-audited); writer rewrites carry `mode=self.mode,
  executor=self.executor`.
- `_ai_share`: `tax = self._REWRITE_TAX.get(self.mode, self._REWRITE_TAX["standard"])`.
- `_personal_paragraph` — capability-driven, single-executor:

```python
    def _personal_paragraph(self, language, title) -> dict:
        """Real-or-stub opening paragraph. Real ONLY when the run's executor can
        web-search (commercial) AND a personality dossier exists; every HirschAI
        run stubs — loudly, never silently (the stub keeps the export blocker
        armed). Research, write, and audit all run on the run's executor."""
        if not self.executor.supports_web_search:
            return self._stub()
        personality = self._personality_dossier()
        if not personality:
            return self._stub()
        company = self._recipient()["company"]
        research = CompanyResearcher(
            company, self._posting_text(), executor=self.executor, language=language
        ).research()
        if not research["ok"]:
            return self._stub()
        text = PersonalParagraphWriter(
            posting_text=self._posting_text(), title=title, language=language,
            company_dossier=research["dossier"], personality_dossier=personality,
            executor=self.executor,
        ).write()
        if not text:
            return self._stub()
        grounding = ParagraphGroundingCheck(
            text, research["dossier"], personality, executor=self.executor
        ).critique()
        return {"text": text, "is_stub": False,
                "sources": research["sources"], "grounding": grounding}
```

(The `blank`/"slot not requested" path dies with the opt-in flag — the slot always exists.)
`_personality_dossier(self)` calls `prof.ensure_dossier(executor=self.executor)`.

### 6. `jac/research.py`

```python
class CompanyResearcher:
    def __init__(self, company, posting_text, *, executor, language="en", max_uses=5):
        ...
    def research(self) -> dict:
        if not self.company:
            return self._empty()
        if not self.executor.supports_web_search:
            logger.info("CompanyResearcher: %s cannot web-search; skipping",
                        self.executor.provider)
            return self._empty()
        try:
            res = self.executor.web_search(prompt=self._prompt(), max_uses=self.max_uses)
        ...unchanged...
```

(the `can_web_search`/`web_search` module imports die.)

### 7. `jac/vectors.py` + `jac/signals.py` + `jac/tasks.sync_user_vectors`

vectors: every `alias` parameter dies; the collection follows the tower's embed model:

```python
def collection_for() -> str | None:
    """The tower embed model's collection (floors + vectors are embedder-specific;
    a model switch on the HirschAI row lands in a fresh collection). None -> off."""
    from llm_connector.conf import hirschai_row

    try:
        cfg = hirschai_row().to_config_dict()
    except Exception:  # noqa: BLE001 — no row -> classic path
        return None
    model = cfg.get("embed_model") or cfg.get("model") or ""
    return store.collection_name(model) if model else None
```

`reconcile(user, doc, desired, *, delete_orphans=False)` and
`ranked_via_store(query_text, entries, *, doc, user)` lose `alias`; their `embed(inputs=…)`
calls lose `alias=`/`user=`. `sync_alias` is deleted. In `Embed.ranked_vectors`, the
`ranked_via_store(...)` call drops `alias=self.alias`.

signals — the privacy gate:

```python
def queue_vector_sync(sender, instance, **kwargs):
    from vector_store import store

    if not store.is_enabled():
        return
    # Privacy gate (2026-07-16): a user whose DEFAULT executor is commercial has
    # opted their data OUT of the tower — no background embedding for them. If they
    # later run HirschAI explicitly, the query-time reconcile embeds on demand;
    # that run is consent.
    from llm_connector.models import LLMConfig

    if LLMConfig.objects.filter(user_id=instance.user_id, default=True).exists():
        return
    user_id = instance.user_id
    transaction.on_commit(lambda: _enqueue(user_id))
```

`sync_user_vectors` (tasks): drop `alias = vectors.sync_alias(user_id)` and the `alias`
arguments to `reconcile`.

### 8. `jac/tasks.py` — `generate_run`

Imports: drop `is_free_alias`/`pick_alias`/`mode_to_grade`; add
`from llm_connector.executor import Executor` and `from jac.filter import GenerationError`.
Inside the `retry_reporter` block:

```python
            executor = Executor(run.provider or "ollama", run.model or None, user)
            mode = run.mode or Mode.standard

            # 1. Tailor the CV.
            _progress(run, "filtering CV")
            cv = CV(...unchanged scoping...)
            cv.apply_selection(
                cv.filter_cv(
                    jp.posting_text,
                    mode=mode,
                    executor=executor,
                    pinned=application.pinned_entries,  # guide 3 adds the field
                )
            )

            # 2. Extract the recipient address (posting text only — the web-search
            # variant is dead). Same executor as everything else in this run.
            _progress(run, "reading posting")
            extracted = AddressExtract(jp.posting_text, executor=executor).extract()
            ...unchanged persistence...

            # 3. Build the cover letter (audits included, same executor).
            _progress(run, "writing letter")
            letter = CoverLetter(
                user, jp, cv, address=addr, mode=mode, executor=executor
            ).build()

        result = {
            "meta": {"mode": mode, "provider": executor.provider,
                     "model": executor.model or ""},
            "cv": serialize_cv_selection(cv),
            "cover_letter": letter,
        }
```

New except-arm before the generic one (and the timeout message loses "pick a lighter grade" →
*"try again or switch executor"*):

```python
    except GenerationError as exc:
        logger.warning("generate_run %s: %s", run_id, exc)
        _fail(run, str(exc))
```

### 9. `jac/serializers.py`

Create serializer — `mode`/`provider`/`model` in, executor validated through the connector:

```python
class GenerationRunCreateSerializer(ScopeRelatedToUserMixin, serializers.ModelSerializer):
    user_scoped_fields = ("job_application",)
    mode = serializers.CharField(required=False, allow_blank=True, default="")
    provider = serializers.CharField(required=False, allow_blank=True, default="")
    model = serializers.CharField(required=False, allow_blank=True, default="")

    class Meta:
        model = GenerationRun
        fields = ["job_application", "mode", "provider", "model",
                  "domains", "started", "ended", "min_skill_proficiency"]

    def validate(self, attrs):
        mode = normalize_mode((attrs.get("mode") or "").strip())
        if mode == Mode.manual:
            raise serializers.ValidationError(
                {"mode": ["manual never runs a generation — curate the application directly."]}
            )
        try:
            executor = resolve_executor(
                self.context["request"].user,
                attrs.get("provider", ""),
                attrs.get("model", ""),
            )
        except ExecutorError as exc:
            raise serializers.ValidationError({"provider": [str(exc)]})
        if mode == Mode.high and executor.is_hirschai:
            raise serializers.ValidationError(
                {"mode": ["high mode needs a commercial executor — HirschAI runs standard."]}
            )
        attrs["mode"] = mode
        attrs["provider"] = executor.provider
        attrs["model"] = executor.model or ""
        return attrs
```

Read serializers (`GenerationRunSerializer` / `GenerationRunSummarySerializer`): drop the
`grade` SerializerMethodFields and `alias`/`personal_paragraph`/`verify_grounding`/`evaluation`/
`score` from `fields`; add `provider`, `model` (both keep `mode`). Imports: drop
`KNOWN_MODE_INPUTS`/`mode_to_grade`, add
`from llm_connector.conf import ExecutorError, resolve_executor`.

### 10. `jac/views.py`

- Imports: delete `AddressSearch`, `can_web_search`, `get_alias_strength`; add
  `from llm_connector.conf import ExecutorError, default_executor, resolve_executor`.
- **Delete the whole `find_address` action** (address web search is deprecated).
- Module-level enqueue helper (used twice):

```python
def _enqueue_run(run: GenerationRun) -> None:
    """Queue the Celery task and remember its id (what cancel() revokes)."""
    async_result = generate_run.apply_async(args=[run.pk], expires=GENERATION_EXPIRES_S)
    run.task_id = async_result.id
    run.save(update_fields=["task_id"])
```

- `GenerationRunViewSet.perform_create` shrinks to `run = serializer.save();
  _enqueue_run(run); self._created = run`.
- **Auto-run on application create** — `JobApplicationViewSet`:

```python
    def perform_create(self, serializer):
        application = serializer.save()
        # Auto-run (2026-07-16): a new application immediately generates on the
        # user's default executor — their default commercial provider, else
        # HirschAI when reachable. No executor -> the manual flow, no run. Only
        # ever on CREATE; re-runs are explicit.
        executor = default_executor(self.request.user)
        if executor is None:
            return
        run = GenerationRun.objects.create(
            job_application=application,
            mode=Mode.standard,
            provider=executor.provider,
            model=executor.model or "",
        )
        _enqueue_run(run)
```

- `rewrite` / `chat`: the `alias` request key → `provider`/`model`, resolved up front (shared
  shape):

```python
        try:
            executor = resolve_executor(
                request.user,
                request.data.get("provider", ""),
                request.data.get("model", ""),
            )
        except ExecutorError as exc:
            return Response({"provider": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
```

  then `ParagraphRewrite(text, instruction=…, language=…, executor=executor)` /
  `LetterChat(body, messages, posting_text=…, language=…, executor=executor)`. **The chat
  strength gate is deleted** — any resolvable executor chats; docstring: *"Any executor may
  chat — the model pick is the user's call."* `_chat_problem` stays as is.

### 11. `spa/models.py`

`ensure_dossier(self, *, alias="default", user=None)` → `ensure_dossier(self, *, executor)`;
its internal distiller call passes `executor=` through the same sweep as §4. spa-internal
rebuild triggers use `default_executor(self.user)` and skip the rebuild when it returns None
(the dossier is rebuilt lazily on next use — ` ensure_dossier` is already
"build-if-stale"). Sweep: `grep -rn "ensure_dossier\|alias=" backend/spa --include="*.py"`.

### 12. Migrations + DB reset

All three apps' migrations are already deleted. When the guide is typed:

```bash
rm -f backend/db.sqlite3
python manage.py makemigrations llm_connector jac spa
python manage.py migrate
```

The HirschAI system row self-creates on first use (`hirschai_row()`); no data migration, no
fixtures.

## Tests — on disk, red now

- `jac/tests/test_models.py` — Mode vocabulary + `normalize_mode`, `GenerationRun` shape and
  defaults, `user`/`posting` properties, application transitions (lean survivors).
- `jac/tests/test_api.py` — generation-create validation matrix (default executor, high-on-tower
  400, unconfigured provider 400, unknown model 400, manual 400, blank mode → standard), run
  read shape (`provider`/`model`/`mode`), auto-run on application create (probe + celery
  mocked; PATCH never triggers), cancel, chat/rewrite executor resolution + no strength gate.
- `jac/tests/test_pipeline.py` — **new file** (deterministic pipeline units; models/views/
  prompts didn't cover CVFilter/CoverLetter logic): the mode ladder with patched scorers
  (manual = zero calls, high→instruct degrade, commercial instruct failure raises
  `GenerationError`, tower failure falls to embed), pinned-entry guarantees + the high-mode
  warning, `_ai_share` taxes, `editable_body`, paragraph stubbing on non-web executors.
- `jac/tests/test_prompts.py` — the live statistical suite against HirschAI (skips loudly when
  the tower is down); see guide notes in the file header: `JAC_PROMPT_RUNS` /
  `JAC_PROMPT_PASS_RATE` env knobs, default 5 runs / 60 % pass.

## Verification

1. Full backend suite: `python manage.py test llm_connector jac` — clean wall of dots (prompt
   tests skip as `s` when ollama is down; run them with the tower up at least once).
2. Dead-vocabulary grep, empty outside `migrations/`:
   ```bash
   grep -rn "alias\|instruct\b\|conversational\b" backend/jac --include="*.py" | grep -v migrations | grep -viE "Instruct|Conversational"   # class names survive; the words as VALUES must not
   grep -rn "verify_grounding\|verifier_alias\|research_alias\|personal_paragraph=\|find_address\|AddressSearch\|mode_to_grade\|Grade\b" backend --include="*.py" | grep -v migrations
   ```
3. Privacy grep — embedding call sites are tower-gated only:
   ```bash
   grep -rn "embed(" backend/jac backend/llm_connector --include="*.py" | grep -v migrations | grep -v tests
   ```
   Every hit must be `llm_connector.embed()` reached from `Embed`/`SnippetEmbed`/`vectors`, all
   behind an `is_hirschai` gate or the sync task.
4. Live smoke via the API (SPA is known-broken until the frontend phase): create an application
   with `posting_text` while ollama runs → a `standard` run auto-appears and completes;
   `POST /api/jac/generations/` with `{"mode": "high", "provider": "ollama"}` → 400;
   with a configured Anthropic key + `{"provider": "anthropic", "mode": "high"}` → tailored CV +
   letter with real audits and (with a personality dossier) a real personal paragraph.

## Results

<!-- Human fills this in: raw test output, observed issues, what works. -->
