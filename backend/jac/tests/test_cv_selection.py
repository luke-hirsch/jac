"""CVFilter selection rungs (light / standard / strong), routing, floors."""

from unittest.mock import patch

from django.test import TestCase

from jac.filter import CVFilter

from ._helpers import _entry


class CVSelectionTests(TestCase):
    """CVFilter propagation + per-section drop (light rung), with injected scores."""

    def _entries(self):
        return [
            _entry("job:1", "job", refs=["skill:1"]),
            _entry("skill:1", "skill"),
            _entry("skill:2", "skill"),
            _entry("certification:1", "certification", refs=["skill:1"]),
            _entry("language:1", "language"),
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
        entries = [_entry(f"skill:{i}", "skill") for i in range(1, 8)]
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
        entries = [_entry("job:1", "job"), _entry("job:2", "job")]
        f = CVFilter(job_post_text="x", entries=entries, grade="light")
        out = f._select({"job:1": 0.3, "job:2": 0.8})
        self.assertEqual([e["id"] for e in out["job"]], ["job:2", "job:1"])


class CVFavouriteBonusTests(TestCase):
    """CVFilter applies a small post-propagation nudge to favourites."""

    def _filter(self, entries):
        return CVFilter(job_post_text="x", entries=entries, grade="light")

    def test_bonus_added_and_reranks(self):
        entries = [
            _entry("job:1", "job", favourite=True),
            _entry("job:2", "job"),
        ]
        out = self._filter(entries)._select({"job:1": 0.40, "job:2": 0.40})
        scores = {e["id"]: e["score"] for e in out["job"]}
        self.assertAlmostEqual(scores["job:1"], 0.45, places=4)
        self.assertAlmostEqual(scores["job:2"], 0.40, places=4)
        # tie broken in the favourite's favour.
        self.assertEqual(out["job"][0]["id"], "job:1")

    def _edus(self, fav_score):
        # Two strong educations + one favourite at `fav_score`; education floor 0.15,
        # min_keep 2 (already satisfied by the two strong ones).
        return [
            _entry("education:1", "education"),
            _entry("education:2", "education"),
            _entry("education:3", "education", favourite=True),
        ], {"education:1": 0.9, "education:2": 0.9, "education:3": fav_score}

    def test_bonus_cannot_resurrect_zero_scored_favourite(self):
        entries, base = self._edus(0.0)
        out = self._filter(entries)._select(base)
        kept = {e["id"] for e in out["education"]}
        # 0.0 + 0.05 = 0.05 < 0.15 floor -> stays dropped.
        self.assertNotIn("education:3", kept)

    def test_bonus_lifts_borderline_favourite(self):
        entries, base = self._edus(0.12)
        out = self._filter(entries)._select(base)
        kept = {e["id"] for e in out["education"]}
        # 0.12 + 0.05 = 0.17 >= 0.15 floor -> crosses.
        self.assertIn("education:3", kept)


class CVFilterFloorsTests(TestCase):
    """CVFilter._floors merges config embed_floors over _SECTION_POLICY defaults,
    and _select drops by the resolved floor."""

    def _entries(self):
        return [_entry(f"skill:{i}", "skill") for i in range(1, 8)]

    def test_floors_merge_config_over_defaults(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="light")
        with patch("jac.filter.get_embed_floors", return_value={"skill": 0.55}):
            floors = f._floors()
        self.assertEqual(floors["skill"], 0.55)  # overridden by config
        self.assertEqual(floors["job"], 0.20)  # default kept

    def test_select_uses_overridden_floor(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="light")
        # 3 skills clear the default 0.35 floor; the other 4 sit at 0.20 (below default).
        base = {f"skill:{i}": (0.5 if i <= 3 else 0.20) for i in range(1, 8)}
        # Default would keep 3 + min_keep top-up to 5; lower the floor and all 7 clear it.
        with patch("jac.filter.get_embed_floors", return_value={"skill": 0.15}):
            out = f._select(base)
        self.assertEqual(len(out["skill"]), 7)

    def test_default_floor_when_no_override(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="light")
        base = {f"skill:{i}": (0.5 if i <= 3 else 0.20) for i in range(1, 8)}
        with patch("jac.filter.get_embed_floors", return_value={}):
            out = f._select(base)
        # 3 above the 0.35 default + min_keep(5) tops up to 5.
        self.assertEqual(len(out["skill"]), 5)


class CVSelectRankedTests(TestCase):
    """CVFilter._select_ranked (standard rung): keep-by-label, favourites pinned,
    min_keep honoured."""

    def _filter(self, entries):
        return CVFilter(job_post_text="x", entries=entries, grade="standard")

    def test_keeps_relevant_drops_zero_label(self):
        # 4 jobs (min_keep 3): two rated relevant, two rated 0. min_keep forces a 3rd back.
        entries = [_entry(f"job:{i}", "job") for i in range(1, 5)]
        labels = {"job:1": 3, "job:2": 2, "job:3": 0, "job:4": 0}
        out = self._filter(entries)._select_ranked(labels)
        kept = [e["id"] for e in out["job"]]
        # two relevant kept + one zero-rated topped up to satisfy min_keep(3); ranked desc.
        self.assertEqual(kept[:2], ["job:1", "job:2"])
        self.assertEqual(len(kept), 3)

    def test_skills_count_varies_with_fit(self):
        # 8 skills (min_keep 5): 6 rated relevant -> all 6 kept (count tracks fit, no clamp).
        entries = [_entry(f"skill:{i}", "skill") for i in range(1, 9)]
        labels = {f"skill:{i}": (2 if i <= 6 else 0) for i in range(1, 9)}
        out = self._filter(entries)._select_ranked(labels)
        self.assertEqual(len(out["skill"]), 6)

    def test_favourite_pinned_despite_zero_label(self):
        # project min_keep 0; a 0-rated favourite is still kept (pinned), a 0-rated non-fav isn't.
        entries = [
            _entry("project:1", "project", favourite=True),
            _entry("project:2", "project"),
        ]
        out = self._filter(entries)._select_ranked({"project:1": 0, "project:2": 0})
        kept = {e["id"] for e in out["project"]}
        self.assertEqual(kept, {"project:1"})

    def test_languages_never_dropped(self):
        out = self._filter([_entry("language:1", "language")])._select_ranked(
            {"language:1": 0}
        )
        self.assertEqual([e["id"] for e in out["language"]], ["language:1"])

    def test_ranked_descending_by_label(self):
        entries = [_entry(f"job:{i}", "job") for i in range(1, 4)]
        out = self._filter(entries)._select_ranked({"job:1": 1, "job:2": 3, "job:3": 2})
        self.assertEqual([e["id"] for e in out["job"]], ["job:2", "job:3", "job:1"])

    def test_score_is_the_label(self):
        out = self._filter([_entry("job:1", "job")])._select_ranked({"job:1": 2})
        self.assertEqual(out["job"][0]["score"], 2)


class CVSelectHolisticTests(TestCase):
    """CVFilter._select_holistic (strong rung): model's selection + guardrails
    (favourites, min_keep, langs)."""

    def _filter(self, entries):
        return CVFilter(job_post_text="x", entries=entries, grade="strong")

    def _sel(self, *ids):
        return [{"id": i, "why": f"why {i}"} for i in ids]

    def test_keeps_selected_in_order_drops_rest(self):
        # projects: min_keep 0 -> unselected are genuinely dropped.
        entries = [_entry(f"project:{i}", "project") for i in range(1, 4)]
        out = self._filter(entries)._select_holistic(
            self._sel("project:3", "project:1")
        )
        self.assertEqual([e["id"] for e in out["project"]], ["project:3", "project:1"])

    def test_reason_carried_and_score_none(self):
        out = self._filter([_entry("project:1", "project")])._select_holistic(
            self._sel("project:1")
        )
        self.assertEqual(out["project"][0]["reason"], "why project:1")
        self.assertIsNone(out["project"][0]["score"])

    def test_favourite_pinned_when_model_omits_it(self):
        entries = [
            _entry("project:1", "project"),
            _entry("project:2", "project", favourite=True),
        ]
        out = self._filter(entries)._select_holistic(self._sel("project:1"))
        kept = {e["id"] for e in out["project"]}
        self.assertEqual(kept, {"project:1", "project:2"})

    def test_min_keep_tops_up_from_remainder(self):
        # jobs min_keep 3; model picks only 1 -> two more topped up from natural order.
        entries = [_entry(f"job:{i}", "job") for i in range(1, 5)]
        out = self._filter(entries)._select_holistic(self._sel("job:2"))
        kept = [e["id"] for e in out["job"]]
        self.assertEqual(kept[0], "job:2")  # model's pick stays first
        self.assertEqual(len(kept), 3)  # topped up to min_keep

    def test_count_varies_with_fit_no_clamp(self):
        # skills min_keep 5; model picks 7 -> all 7 kept (never clamped to a target).
        entries = [_entry(f"skill:{i}", "skill") for i in range(1, 9)]
        out = self._filter(entries)._select_holistic(
            self._sel(*[f"skill:{i}" for i in range(1, 8)])
        )
        self.assertEqual(len(out["skill"]), 7)

    def test_languages_never_dropped(self):
        entries = [_entry(f"language:{i}", "language") for i in range(1, 3)]
        out = self._filter(entries)._select_holistic(self._sel("language:1"))
        self.assertEqual(
            {e["id"] for e in out["language"]}, {"language:1", "language:2"}
        )


class CVFilterRoutingTests(TestCase):
    """output() routing across rungs: strong degrades to standard degrades to
    light, and standard degrades to light, each on an empty result."""

    def _entries(self):
        return [_entry("job:1", "job"), _entry("job:2", "job")]

    # -- standard grade --------------------------------------------------

    def test_standard_uses_ranked_selection(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="standard")
        with patch.object(
            CVFilter, "_standard_scores", return_value={"job:1": 3, "job:2": 1}
        ):
            out = f.output()
        # ranked by label desc; scores are the labels (not cosine).
        self.assertEqual([e["id"] for e in out["job"]], ["job:1", "job:2"])
        self.assertEqual(out["job"][0]["score"], 3)

    def test_standard_falls_back_to_light_when_scorer_empty(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="standard")
        with (
            patch.object(CVFilter, "_standard_scores", return_value={}),
            patch.object(
                CVFilter, "_light_scores", return_value={"job:1": 0.9, "job:2": 0.2}
            ),
        ):
            out = f.output()
        # light path: floored selection, cosine scores preserved.
        self.assertEqual(out["job"][0]["score"], 0.9)

    # -- strong grade ----------------------------------------------------

    def test_strong_uses_holistic_selection(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="strong")
        with patch.object(
            CVFilter, "_strong_selection", return_value=[{"id": "job:2", "why": "best"}]
        ):
            out = f.output()
        self.assertEqual(out["job"][0]["id"], "job:2")
        self.assertEqual(out["job"][0]["reason"], "best")

    def test_strong_falls_back_to_standard(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="strong")
        with (
            patch.object(CVFilter, "_strong_selection", return_value=[]),
            patch.object(
                CVFilter, "_standard_scores", return_value={"job:1": 3, "job:2": 1}
            ),
        ):
            out = f.output()
        self.assertEqual(out["job"][0]["id"], "job:1")
        self.assertEqual(out["job"][0]["score"], 3)  # standard labels, not holistic

    def test_strong_falls_back_to_light_when_both_empty(self):
        f = CVFilter(job_post_text="x", entries=self._entries(), grade="strong")
        with (
            patch.object(CVFilter, "_strong_selection", return_value=[]),
            patch.object(CVFilter, "_standard_scores", return_value={}),
            patch.object(
                CVFilter, "_light_scores", return_value={"job:1": 0.9, "job:2": 0.2}
            ),
        ):
            out = f.output()
        self.assertEqual(out["job"][0]["score"], 0.9)  # cosine -> light path
