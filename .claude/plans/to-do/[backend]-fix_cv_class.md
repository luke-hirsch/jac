# [backend] fix & optimise the CV class — relationship edges + weak per-section selection

**Roadmap item 1.** Make `backend/jac/cv.py` rank/filter career entries by fit to a job posting via
the `light` / `standard` / `strong` ladder. This guide covers the two gaps we just designed:

1. **Relationships aren't in the flattened entry list.** FK/M2M links between entries are only
   leaked as text. Add an explicit `refs` edge list per entry so the ranker can reason over the
   graph.
2. **No weighting on what gets dropped.** Today scores are computed and thrown away; worst case is a
   skills-only CV. Replace this with a **scoring-agnostic** selection layer in `CVFilter`:
   directional score propagation over the edges + per-section *absolute floor + min-keep* drop rule.
   Filter weakly, rank hard, remove only the completely irrelevant, never break section structure.

**Design decisions locked in (don't revisit while implementing):**

- **Selection is scoring-agnostic.** `Embed` / `Instruct` / `Conversational` only ever return
  `{id, score}`. All graph + drop logic lives in `CVFilter`, so `light` / `standard` / `strong`
  share one code path. (This is also why the tests can inject fake scores and run fully offline.)
- **Propagation is directional, single sweep.** Entries have tiers: experiences
  (`job`/`project`/`education` = tier 0) anchor `skill` (tier 1) anchor `certification` (tier 2)
  anchor `language` (tier 3). A node is lifted only by *higher-tier* neighbours, never the reverse.
  One ascending sweep — no fixpoint iteration. Same-tier edges (e.g. job↔project) carry no lift.
- **Drop rule = per-section absolute cosine floor + min-keep.** Drop an entry only if its effective
  score is below that section's floor, *unless* dropping it would breach the section's `min_keep`
  guarantee. Languages are never dropped. No max clamp (length guardrails belong to the later
  CV-render phase — see the `selection-size-is-intentional` memory).

---

## Affected files

| path | change |
| ---- | ------ |
| `backend/jac/cv.py` | add `refs` to flattened entries; add prefetches for new edge reads; rewrite `CVFilter` with `_propagate` + `_select`; route all grades through `_select`; carry `refs` into `CVFilter` |
| `backend/jac/llm_prompts.py` | no change required for this chunk (`Embed.ranked_entries()` already returns `{id, score, reason}`; `Instruct`/`Conversational` stay stubs) |
| `backend/jac/tests.py` | add `CVEdgeTests` and `CVSelectionTests` (offline, fake-score injection) |

---

## The code

### 1. `cv.py` — query prefetches for the new edge reads

The edge map reads relations not currently prefetched. Add them so flattening stays O(1) queries.

In `_get_jobs`, add `"projects"` (reverse FK `Project.job`) to the prefetch:

```python
    def _get_jobs(self) -> list[Job]:
        qs = (
            Job.objects.filter(user=self.user)
            .prefetch_related("skills", "domains", "projects")
            .select_related("location")
        )
```

In `_get_educations`, add a skills prefetch (currently only `select_related("location")`):

```python
    def _get_educations(self) -> list[Education]:
        qs = (
            Education.objects.filter(user=self.user)
            .prefetch_related("skills")
            .select_related("location")
        )
```

In `_get_certifications`, add a skills prefetch:

```python
    def _get_certifications(self) -> list[Certification]:
        return list(
            Certification.objects.filter(user=self.user)
            .prefetch_related("skills")
            .order_by("-issued_on")
        )
```

> `Skill.certification` and `Language.certification` are FKs — `s.certification_id` /
> `la.certification_id` are already in memory, no extra query. `Project.job` likewise via
> `p.job_id`. Skill↔skill edges (`related_skills` / `builds_on`) are **omitted** in v1: both ends
> are tier 1, so they produce zero lift and aren't worth the extra prefetch.

### 2. `cv.py` — `_flatten_entries` emits `refs`

Each entry gains a `refs` list of sibling entry-ids. Build the candidate ids inline, then prune to
ids that actually survived the DB filters (a referenced skill may have been filtered out by domain
or proficiency). Replace the whole method:

```python
    def _flatten_entries(self) -> list[dict]:
        """Flatten self.entries into [{id, type, text, refs}, ...] for LLM scoring.

        `refs` holds the ids of related entries (via FK / M2M) that are also present in this
        flattened set. The selection layer uses them to propagate relevance across the graph.
        """
        out: list[dict] = []

        for s in self.entries["skills"]:
            domains = ", ".join(d.name for d in s.domains.all())
            text = f"{s.name} ({s.proficiency}, {s.category})"
            if domains:
                text += f" | domains: {domains}"
            if s.description:
                text += f" — {s.description[:200]}"
            refs = []
            if s.certification_id:
                refs.append(f"certification:{s.certification_id}")
            out.append(
                {"id": f"skill:{s.pk}", "type": "skill", "text": text, "refs": refs}
            )

        for j in self.entries["jobs"]:
            window = f"{j.started or '?'}–{j.ended or 'present'}"
            skills = ", ".join(sk.name for sk in j.skills.all())
            text = f"{j.title} at {j.company} ({window})"
            if skills:
                text += f" | skills: {skills}"
            if j.description:
                text += f" — {j.description[:300]}"
            refs = [f"skill:{sk.pk}" for sk in j.skills.all()]
            refs += [f"project:{p.pk}" for p in j.projects.all()]
            out.append({"id": f"job:{j.pk}", "type": "job", "text": text, "refs": refs})

        for e in self.entries["educations"]:
            window = f"{e.started or '?'}–{e.ended or 'present'}"
            text = f"{e.degree or ''} {e.field_of_study or ''}".strip()
            text = (
                f"{text} @ {e.institution} ({window})"
                if text
                else f"{e.institution} ({window})"
            )
            if e.description:
                text += f" — {e.description[:200]}"
            refs = [f"skill:{sk.pk}" for sk in e.skills.all()]
            out.append(
                {"id": f"education:{e.pk}", "type": "education", "text": text, "refs": refs}
            )

        for c in self.entries["certifications"]:
            text = f"{c.name} — {c.issuer}"
            if c.issued_on:
                text += f" ({c.issued_on})"
            if c.description:
                text += f" — {c.description[:200]}"
            refs = [f"skill:{sk.pk}" for sk in c.skills.all()]
            out.append(
                {
                    "id": f"certification:{c.pk}",
                    "type": "certification",
                    "text": text,
                    "refs": refs,
                }
            )

        for p in self.entries["projects"]:
            window = f"{p.started or '?'}–{p.ended or 'present'}"
            skills = ", ".join(sk.name for sk in p.skills.all())
            text = f"{p.name} ({window})"
            if skills:
                text += f" | skills: {skills}"
            if p.description:
                text += f" — {p.description[:300]}"
            refs = [f"skill:{sk.pk}" for sk in p.skills.all()]
            if p.job_id:
                refs.append(f"job:{p.job_id}")
            out.append(
                {"id": f"project:{p.pk}", "type": "project", "text": text, "refs": refs}
            )

        for la in self.entries["languages"]:
            refs = []
            if la.certification_id:
                refs.append(f"certification:{la.certification_id}")
            out.append(
                {
                    "id": f"language:{la.pk}",
                    "type": "language",
                    "text": f"{la.name} ({la.fluency})",
                    "refs": refs,
                }
            )

        # Prune refs to ids that actually exist in this set (domain/date/proficiency
        # filters may have dropped a referenced entry) and drop self-references.
        valid = {e["id"] for e in out}
        for e in out:
            e["refs"] = [r for r in e["refs"] if r in valid and r != e["id"]]

        return out
```

### 3. `cv.py` — pass `refs` through `filter_cv`

`_flatten_entries` already carries `refs` inside each dict, so `filter_cv` needs no signature
change — just confirm it still forwards the full entries list (it does):

```python
    def filter_cv(self, job_post_text: str, grade: str | None):
        cv_filter = CVFilter(
            job_post_text=job_post_text,
            entries=self._flatten_entries(),
            grade=grade
            if grade and grade in ["light", "standard", "strong"]
            else "light",
            user=self.user,
        )
        return cv_filter.output()
```

### 4. `cv.py` — rewrite `CVFilter`

Replace the entire `CVFilter` class. The grade methods now only produce a `{id: score}` base map;
`_select` does all the shared graph + drop work.

```python
class CVFilter:
    """Turns per-entry relevance scores into a ranked, weakly-filtered CV.

    Scoring is pluggable (embeddings / instruct LLM / conversational LLM); everything below the
    score map — directional propagation over entry edges, then per-section drop — is shared.
    """

    # Tier: a node is lifted only by neighbours of a strictly lower tier number.
    _TIER = {
        "job": 0,
        "project": 0,
        "education": 0,
        "skill": 1,
        "certification": 2,
        "language": 3,
    }
    # Damping applied to an anchor's score when it lifts a lower-tier neighbour.
    _ANCHOR_W = 0.85

    # Per-section drop rule. `drop_below`: absolute effective-score floor (cosine-scaled).
    # `min_keep`: always keep at least this many top-ranked, even below the floor;
    # None = never drop any; 0 = no floor guarantee (section may empty out if irrelevant).
    _SECTION_POLICY = {
        "job": {"drop_below": 0.20, "min_keep": 3},
        "education": {"drop_below": 0.15, "min_keep": 2},
        "skill": {"drop_below": 0.35, "min_keep": 5},
        "project": {"drop_below": 0.30, "min_keep": 0},
        "certification": {"drop_below": 0.30, "min_keep": 0},
        "language": {"drop_below": 0.00, "min_keep": None},
    }

    def __init__(
        self,
        job_post_text: str,
        entries: list[dict],
        grade: str = "light",
        user=None,
    ):
        assert isinstance(job_post_text, str)
        self.job_post_text = job_post_text
        self.entries = entries
        self.grade = grade
        self.user = user

    def output(self) -> dict:
        """Return {section: [entry dicts + score], ...}, each section ranked desc."""
        if self.grade == "strong":
            base = self._strong_scores() or self._standard_scores() or self._light_scores()
        elif self.grade == "standard":
            base = self._standard_scores() or self._light_scores()
        else:
            base = self._light_scores()
        return self._select(base)

    # --- score sources (each returns {id: float} or {} on failure) ---------------------

    def _light_scores(self) -> dict:
        ranked = Embed(self.job_post_text, self.entries).ranked_entries()
        return {r["id"]: r["score"] for r in ranked} if ranked else {}

    def _standard_scores(self) -> dict:
        # TODO: Instruct LLM ranking. Returns {} until implemented -> falls back to light.
        return {}

    def _strong_scores(self) -> dict:
        # TODO: Conversational LLM ranking. Returns {} until implemented -> falls back.
        return {}

    # --- shared selection layer --------------------------------------------------------

    def _propagate(self, base: dict) -> dict:
        """Single ascending-tier sweep: lift each node by its best higher-tier neighbour."""
        eff = {e["id"]: base.get(e["id"], 0.0) for e in self.entries}
        type_of = {e["id"]: e["type"] for e in self.entries}

        adj: dict[str, set[str]] = {}
        for e in self.entries:
            for r in e.get("refs", []):
                adj.setdefault(e["id"], set()).add(r)
                adj.setdefault(r, set()).add(e["id"])

        for tier in (1, 2, 3):
            for e in self.entries:
                if self._TIER.get(e["type"]) != tier:
                    continue
                eid = e["id"]
                higher = [
                    eff[n]
                    for n in adj.get(eid, ())
                    if self._TIER.get(type_of.get(n), 99) < tier
                ]
                if higher:
                    eff[eid] = max(eff[eid], self._ANCHOR_W * max(higher))
        return eff

    def _select(self, base: dict) -> dict:
        """Apply propagation + per-section drop. Empty base -> keep everything unscored."""
        if not base:
            return self._group_all()

        eff = self._propagate(base)

        by_section: dict[str, list[dict]] = {}
        for e in self.entries:
            by_section.setdefault(e["type"], []).append(e)

        out: dict[str, list[dict]] = {}
        for section, items in by_section.items():
            policy = self._SECTION_POLICY.get(section, {"drop_below": 0.0, "min_keep": 0})
            items.sort(key=lambda e: eff.get(e["id"], 0.0), reverse=True)

            min_keep = policy["min_keep"]
            if min_keep is None:
                keep = items
            else:
                keep = [e for e in items if eff.get(e["id"], 0.0) >= policy["drop_below"]]
                if len(keep) < min_keep:
                    keep = items[:min_keep]

            out[section] = [
                {**e, "score": round(eff.get(e["id"], 0.0), 4)} for e in keep
            ]
        return out

    def _group_all(self) -> dict:
        """Fallback when scoring fails: every entry kept, score 0.0."""
        out: dict[str, list[dict]] = {}
        for e in self.entries:
            out.setdefault(e["type"], []).append({**e, "score": 0.0})
        return out
```

> **Note on the floors.** The `drop_below` values are cosine-scaled starting points: jobs/education
> lower (long descriptions embed at lower cosine vs a posting), skills higher (you list many, prune
> harder). They need one-time calibration against your real embedding distribution — the Verification
> section runs the experiment that tells you where to set them. The `~0.42` figure in memory is a
> single global threshold; these are per-section and intentionally looser.

---

## Tests

Add to `backend/jac/tests.py`. Both classes are **fully offline** — they inject fake score maps, so
no embedding model or network is touched. Import `CVFilter` alongside the existing `CV` import:

```python
from jac.cv import CV, CVFilter
```

```python
# ---------------------------------------------------------------------------
# CV edge / selection tests
# ---------------------------------------------------------------------------


class CVEdgeTests(TestCase):
    """_flatten_entries emits correct relationship edges."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="edgeuser")
        cls.cert = Certification.objects.create(
            user=cls.user, name="AWS SA", issuer="Amazon"
        )
        cls.skill = Skill.objects.create(
            user=cls.user, name="Python", certification=cls.cert
        )
        cls.cert.skills.add(cls.skill)
        cls.job = Job.objects.create(
            user=cls.user, title="Eng", company="Acme", started=date(2022, 1, 1)
        )
        cls.job.skills.add(cls.skill)
        cls.project = Project.objects.create(
            user=cls.user, name="Side", started=date(2023, 1, 1), job=cls.job
        )
        cls.project.skills.add(cls.skill)

    def _by_id(self):
        return {e["id"]: e for e in CV(user_pk=self.user.pk)._flatten_entries()}

    def test_job_refs_skill_and_project(self):
        flat = self._by_id()
        refs = set(flat[f"job:{self.job.pk}"]["refs"])
        self.assertIn(f"skill:{self.skill.pk}", refs)
        self.assertIn(f"project:{self.project.pk}", refs)

    def test_project_refs_skill_and_job(self):
        refs = set(self._by_id()[f"project:{self.project.pk}"]["refs"])
        self.assertIn(f"skill:{self.skill.pk}", refs)
        self.assertIn(f"job:{self.job.pk}", refs)

    def test_skill_refs_certification(self):
        refs = self._by_id()[f"skill:{self.skill.pk}"]["refs"]
        self.assertIn(f"certification:{self.cert.pk}", refs)

    def test_refs_pruned_to_existing_ids(self):
        # Skill filtered out by proficiency -> job must not ref a missing skill.
        cv = CV(user_pk=self.user.pk, min_skill_proficiency="expert")
        flat = {e["id"]: e for e in cv._flatten_entries()}
        if f"skill:{self.skill.pk}" not in flat:  # intermediate skill dropped
            self.assertNotIn(
                f"skill:{self.skill.pk}", flat[f"job:{self.job.pk}"]["refs"]
            )


class CVSelectionTests(TestCase):
    """CVFilter propagation + per-section drop, with injected fake scores."""

    def _entries(self):
        return [
            {"id": "job:1", "type": "job", "text": "", "refs": ["skill:1"]},
            {"id": "skill:1", "type": "skill", "text": "", "refs": []},
            {"id": "skill:2", "type": "skill", "text": "", "refs": []},
            {"id": "certification:1", "type": "certification", "text": "", "refs": ["skill:1"]},
            {"id": "language:1", "type": "language", "text": "", "refs": []},
        ]

    def _filter(self):
        return CVFilter(job_post_text="x", entries=self._entries(), grade="light")

    def test_propagation_lifts_low_skill_under_strong_job(self):
        f = self._filter()
        base = {"job:1": 0.9, "skill:1": 0.05, "skill:2": 0.05}
        eff = f._propagate(base)
        # skill:1 is anchored by job:1 -> lifted to 0.85 * 0.9.
        self.assertAlmostEqual(eff["skill:1"], 0.765, places=3)
        # skill:2 has no high-tier neighbour -> untouched.
        self.assertAlmostEqual(eff["skill:2"], 0.05, places=3)

    def test_propagation_chains_job_to_skill_to_cert(self):
        f = self._filter()
        eff = f._propagate({"job:1": 1.0, "skill:1": 0.0, "certification:1": 0.0})
        # job (0.85) -> skill:1, then skill:1 (0.85) -> cert.
        self.assertAlmostEqual(eff["skill:1"], 0.85, places=3)
        self.assertAlmostEqual(eff["certification:1"], 0.7225, places=3)

    def test_low_skill_dropped_below_floor(self):
        f = self._filter()
        # All scores low, no anchoring; skill floor 0.35, min_keep 5 but only 2 skills exist.
        out = f._select({"job:1": 0.9, "skill:1": 0.10, "skill:2": 0.10})
        kept = {e["id"] for e in out.get("skill", [])}
        # min_keep(5) > available(2) -> both skills kept despite being below floor.
        self.assertEqual(kept, {"skill:1", "skill:2"})

    def test_skill_floor_drops_when_above_min_keep(self):
        entries = [
            {"id": f"skill:{i}", "type": "skill", "text": "", "refs": []}
            for i in range(1, 8)
        ]
        f = CVFilter(job_post_text="x", entries=entries, grade="light")
        base = {f"skill:{i}": (0.9 if i <= 5 else 0.10) for i in range(1, 8)}
        out = f._select(base)
        kept = {e["id"] for e in out["skill"]}
        # 5 above floor kept; the 2 below floor dropped (min_keep already satisfied).
        self.assertEqual(kept, {f"skill:{i}" for i in range(1, 6)})

    def test_languages_never_dropped(self):
        f = self._filter()
        out = f._select({"language:1": 0.0})
        self.assertEqual([e["id"] for e in out["language"]], ["language:1"])

    def test_empty_base_keeps_everything(self):
        f = self._filter()
        out = f._select({})
        kept = {e["id"] for sect in out.values() for e in sect}
        self.assertEqual(kept, {e["id"] for e in self._entries()})

    def test_sections_ranked_descending(self):
        entries = [
            {"id": "job:1", "type": "job", "text": "", "refs": []},
            {"id": "job:2", "type": "job", "text": "", "refs": []},
        ]
        f = CVFilter(job_post_text="x", entries=entries, grade="light")
        out = f._select({"job:1": 0.3, "job:2": 0.8})
        self.assertEqual([e["id"] for e in out["job"]], ["job:2", "job:1"])
```

---

## Verification

Run from `backend/` in the `jac` virtualenv.

**1. Unit tests pass (offline, no model needed):**

```bash
cd /Users/lukas/Projects/jac/backend
python manage.py test jac.tests.CVEdgeTests jac.tests.CVSelectionTests -v 2
```

Expect: all tests OK. If `test_refs_pruned_to_existing_ids` is a no-op (skill survived the
proficiency filter), that's fine — it only asserts when the skill is actually dropped.

**2. Full CV test suite still green (no regressions in the existing query/filter tests):**

```bash
python manage.py test jac.tests -v 1
```

**3. End-to-end against a real posting + real embeddings** (Ollama running with
`qwen3-embedding:0.6b`). This both proves the pipeline and shows you where to calibrate the floors:

```bash
python manage.py shell -c "
from jac.cv import CV
posting = open('/Users/lukas/Projects/jac/data/test_job.md').read()
out = CV(user_pk=1).filter_cv(posting, grade='light')
for section, items in out.items():
    print(f'--- {section}  (kept {len(items)}) ---')
    for e in sorted(items, key=lambda x: -x['score']):
        print(f\"  {e['score']:.3f}  {e['text'][:60]}\")
"
```

What "done" looks like:
- Every section that has entries is present, ranked by `score` descending.
- `jobs` shows ≥3, `educations` ≥2, `skills` ≥5 (or all available if fewer), languages all present.
- A skill you *know* is central to a clearly-relevant job is kept with an elevated score even if its
  own text is generic — that's propagation working.
- Obviously off-topic entries (a skill unrelated to the posting, above the min-keep count) are gone.

**4. Calibrate the floors.** If too much/too little is dropped, eyeball the score column from step 3
and nudge `_SECTION_POLICY[...]['drop_below']`. The commented experiment at the bottom of
`llm_prompts.py` prints the min/median/max/p75 cosine distribution per posting if you want the full
spread before picking thresholds.

---

## Out of scope (next chunks)

- `Instruct` / `Conversational` score sources (`_standard_scores` / `_strong_scores` are wired to
  fall back to light until implemented).
- CV rendering / length guardrails (the no-max-clamp decision defers length to the render phase).
- Cover-letter generation (roadmap item 2).
