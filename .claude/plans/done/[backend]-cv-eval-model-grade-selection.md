# cv_eval: pick the LLM (not just the grade) + per-model embedding selection

## Context

Today the CV pipeline gives you no way to choose *which* configured model runs, and forces the
grade by flag:

- `Instruct.ranked_entries()` calls `complete(prompt=…, user=…)` **without `alias=`**
  (`llm_prompts.py`), so it is hardcoded to the `"default"` alias. Your other `LLMConfig` models are
  unreachable from the CV pipeline — this is the "always falls back to default" you saw.
- `cv_eval --grade` defaults to `light` and is passed straight through; the model's *actual*
  strength (`get_alias_strength`, `conf.py:101`) is never consulted.

Goal: let `cv_eval` pick the model with `--llm <alias>`, derive the grade automatically from the
model when not given, and — when only `--grade` is given — fan out across **all** your configured
models so you can compare them at a fixed grade.

Extra wrinkle agreed this session: **`light` should honour the chosen model's embedding model**, so
you can benchmark different embedders. Because the `light` selection floors (`_SECTION_POLICY`
`drop_below`) are calibrated to *one* embedder's cosine distribution, swapping embedders requires
making those floors **per-model and overridable** (stored in the model's config, wired through
dynamically). Embedding models also need to autodetect to `light` regardless of size.

### Resolution matrix (`cv_eval`)

| `--grade` | `--llm` | runs as |
| --- | --- | --- |
| — | — | alias `default`, grade = `get_alias_strength("default")` |
| — | alias | that alias, grade = `get_alias_strength(alias)` |
| grade | — | **every** alias in the user's `LLMConfig`s, each forced to that grade |
| grade | alias | that alias at that grade |

Forcing a grade onto a model below its detected tier is the point (does a 7b survive `standard`?
does embedder X rank better than Y?), so no skipping. With `light` now alias-aware, `--grade light`
across all models becomes a real **embedder comparison**.

## Decisions (locked)

- `--llm` steers the LLM scorers **and** the `light` embedder (via the alias's `embed_model`).
- Multi-model output: one `findings.md` with an added **model** column; per-run files namespaced by
  alias (`<alias>__<slug>.cv.md` / `.ranks.md`) so models don't overwrite each other.
- Per-model `light` floors live under an **`embed_floors`** key in the resolved config
  (`LLMConfig.extra` for per-user, `settings.LLM[alias]` for the default) — a partial
  `{section: float}` dict merged over the hardcoded `_SECTION_POLICY` defaults. **No migration**
  (`extra` already flows through `to_config_dict`).
- Embedding models autodetect to `light` by name hint, before the size check (so a large embedder
  like `e5-mistral-7b` isn't mis-classed `standard`).
- `cv_test` is untouched (keeps the `default` alias); it can get the same `--llm` later.

## Affected files

| path | change |
| --- | --- |
| `backend/llm_connector/conf.py` | embedding-name detection in `_autodetect_strength`; new `get_embed_floors()` |
| `backend/jac/llm_prompts.py` | `Embed` takes `user` + `alias`, passes them to `embed()` |
| `backend/jac/filter.py` | `CVFilter` takes `alias`; threads it to `Embed`; dynamic floors in `_select` |
| `backend/jac/cv.py` | `filter_cv()` takes `alias`, passes to `CVFilter` |
| `backend/jac/management/commands/cv_eval.py` | `--llm` flag; grade×llm matrix; per-model loop + output |
| `backend/jac/tests.py`, `backend/llm_connector/tests.py` | unit tests (see Tests) |

## The code

### 1. `llm_connector/conf.py`

Add embedding-name detection (tunable hint list) and a floors resolver alongside
`get_alias_strength`:

```python
# Embedding-model name hints. These map to 'light' regardless of size token — an
# embedder only ever does the light rung, and large embedders (e5-mistral-7b) must
# not autodetect to 'standard'. Tune as new embedders show up.
_EMBED_NAME_HINTS = ("embed", "bge", "gte", "e5", "minilm", "nomic")


def _autodetect_strength(provider: str, model: str) -> str:
    name = (model or "").lower()
    if any(hint in name for hint in _EMBED_NAME_HINTS):
        return "light"
    sizes = [float(m) for m in _SIZE_RE.findall(name)]
    if sizes:
        size = max(sizes)
        if size <= 3:
            return "light"
        if size <= 14:
            return "standard"
        return "strong"
    if any(hint in name for hint in _SMALL_NAME_HINTS):
        return "standard"
    return "strong"


def get_embed_floors(alias: str = "default", user=None) -> dict:
    """Per-section cosine drop floors for the light rung, from the resolved config's
    `embed_floors` key. {} when unset or on any resolution error — caller merges over
    its own defaults. Floors are embedder-specific (cosine distributions differ), so
    they live with the model config, not hardcoded in the filter.
    """
    try:
        config = get_alias_config(alias, user=user)
    except Exception:  # noqa: BLE001 — missing/broken config -> defaults
        return {}
    floors = config.get("embed_floors")
    return floors if isinstance(floors, dict) else {}
```

### 2. `jac/llm_prompts.py` — `Embed` honours the alias

```python
    def __init__(self, job_post_text: str, entries: list[dict], user=None, alias: str = "default"):
        self.job_post_text = job_post_text
        self.entries = entries
        self.user = user
        self.alias = alias
        self.flatten_entries = [e.get("text") or "" for e in entries]
```

In `_query`, pass them through:

```python
        return embed(inputs=inputs, alias=self.alias, user=self.user)
```

### 3. `jac/filter.py` — thread alias + dynamic floors

`__init__` gains `alias` (store `self.alias = alias`). `_light_scores` passes it:

```python
    def _light_scores(self) -> dict:
        ranked = Embed(
            self.job_post_text, self.entries, user=self.user, alias=self.alias
        ).ranked_entries()
        return {r["id"]: r["score"] for r in ranked} if ranked else {}
```

Add a floors resolver and use it in `_select` (import at top:
`from llm_connector.conf import get_embed_floors`):

```python
    def _floors(self) -> dict:
        """Per-section cosine floors: config `embed_floors` over _SECTION_POLICY defaults."""
        defaults = {s: p["drop_below"] for s, p in self._SECTION_POLICY.items()}
        return {**defaults, **get_embed_floors(self.alias, user=self.user)}
```

In `_select`, compute `floors = self._floors()` once before the section loop and replace
`policy["drop_below"]` with `floors.get(section, policy.get("drop_below", 0.0))`.

### 4. `jac/cv.py` — `filter_cv` forwards the alias

```python
    def filter_cv(self, job_post_text: str, grade: str | None, alias: str = "default"):
        cv_filter = CVFilter(
            job_post_text=job_post_text,
            entries=self._flatten_entries(),
            grade=grade if grade in ("light", "standard", "strong") else "light",
            user=self.user,
            alias=alias,
        )
        return cv_filter.output()
```

### 5. `cv_eval.py` — `--llm`, matrix, per-model output

Imports:

```python
from llm_connector.conf import get_alias_strength
from llm_connector.models import LLMConfig
```

Pure, testable matrix resolver (module level):

```python
def _resolve_runs(grade, llm, aliases, strength_of):
    """[(alias, grade)] per the grade×llm matrix.

    aliases: user's configured aliases (used only when grade given, no llm).
    strength_of: callable(alias) -> autodetected grade.
    """
    if llm:
        return [(llm, grade or strength_of(llm))]
    if grade:
        return [(a, grade) for a in aliases]
    return [("default", strength_of("default"))]
```

Arguments: `--grade` default **None** (keep `choices=_GRADES`); add
`--llm` (`type=str, default=None, help="LLMConfig alias to use (default: 'default')"`).

In `handle`, after resolving postings, build the run list and loop alias × posting:

```python
        aliases = list(
            LLMConfig.objects.filter(user=opts["user"]).values_list("alias", flat=True)
        ) or ["default"]
        runs = _resolve_runs(
            opts["grade"], opts["llm"], aliases,
            lambda a: get_alias_strength(a, user=opts["user"]),
        )

        rows = [
            self._evaluate(opts["user"], text, slug, grade, alias, out_dir, write, color, opts["show_ranks"])
            for alias, grade in runs
            for slug, text in postings
        ]
```

`_evaluate` gains an `alias` param: pass `alias` to `cv.filter_cv(job_text, grade=grade, alias=alias)`,
add `"model": alias` to the row dict, and namespace the artifacts with the alias prefix:

```python
        stem = f"{_safe(alias)}__{slug}"
        (out_dir / f"{stem}.cv.md").write_text(CvRender(cv).export_md(), encoding="utf-8")
        ...
        self._write_ranks(stem, grade, counts, ranks, out_dir)   # writes <stem>.ranks.md
```

Reporting: `_print_posting` header shows the model; `_write_findings` adds a `model` column
(`| posting | model | grade | total | … |`) and reads `r["model"]`; `_compare` keys prev by
`(r.get("model", "?"), r["posting"])` so models don't collide.

### Setting per-model floors (no code, documented)

To recalibrate `light` for a different embedder, put floors in that model's config `extra`:

```json
{ "embed_model": "bge-large", "strength": "light",
  "embed_floors": { "skill": 0.55, "job": 0.45 } }
```

Unspecified sections fall back to `_SECTION_POLICY` defaults.

## Tests

**`llm_connector/tests.py`**
- `_autodetect_strength`: `bge-large`, `nomic-embed-text`, `e5-mistral-7b` → `light`; existing
  cases (`llama3.2:1b`→light, `qwen2.5:7b`→standard, `70b`→strong, `haiku`→standard) unchanged.
- `get_embed_floors`: returns `{}` when unset / on lookup failure; returns the dict when present
  (override `settings.LLM` with an alias carrying `embed_floors`).

**`jac/tests.py`**
- `Embed` passes `alias`/`user` to `embed()` (patch `jac.llm_prompts.embed`, assert kwargs).
- `CVFilter._floors()` merges config floors over defaults (patch
  `jac.filter.get_embed_floors`); `_select` drops/keeps by the overridden floor.
- `_resolve_runs` matrix: all four cases, with `aliases=["a","b"]` and a fake `strength_of`.

## Verification

From `backend/` with the `jac` venv:

```bash
python manage.py check
python manage.py test llm_connector jac.tests
```

Live:

```bash
# autodetect from the model: a mid model -> standard, no fallback (scores are 0..3 ints)
python manage.py cv_eval --user 1 --job-file data/test_job.md --llm reasoning --show-ranks

# fan out across all your models at a fixed grade -> findings.md has a 'model' column
python manage.py cv_eval --user 1 --job-file data/test_job.md --grade standard

# embedder comparison: light across all models, each using its own embed_model
python manage.py cv_eval --user 1 --job-file data/test_job.md --grade light
```

"Done" looks like: `--llm` actually selects the model (no "falls back to default" warning when the
alias exists); grade is derived when omitted; multi-model runs produce one `findings.md` with a
`model` column and non-colliding `<alias>__<slug>` files; `light` runs embed with the alias's
`embed_model` and respect any `embed_floors` override.

## Note on workflow

You said this is a volatile phase — on approval I'll implement the source directly (not just a
guide). Testing/verification stays with you.
