"""Cover-letter assembly — snippet selection, build, ai_share, grounding."""

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from jac.cover_letter import PERSONAL_STUB, CoverLetter, SnippetSelector, editable_body
from jac.models import Domain, Job, JobPostAddress, ResumeSnippet
from jac.research import CompanyResearcher

from ._helpers import _CoverLetterCVMixin, _muted


class SnippetSelectorTests(_CoverLetterCVMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("snipuser")
        cls.domain = Domain.objects.create(user=cls.user, name="Backend")
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        cls.job.domains.add(cls.domain)
        cls.other_job = Job.objects.create(
            user=cls.user, title="Old", company="Y", started=date(2010, 1, 1)
        )
        K = ResumeSnippet.Kind
        cls.intro = ResumeSnippet.objects.create(
            user=cls.user, title="Intro", content="Hi", kind=K.intro
        )
        cls.closing = ResumeSnippet.objects.create(
            user=cls.user, title="Bye", content="Thanks", kind=K.closing
        )
        cls.body_kept = ResumeSnippet.objects.create(
            user=cls.user,
            title="Achv",
            content="Did X",
            kind=K.achievement,
            job=cls.job,
        )
        cls.body_other = ResumeSnippet.objects.create(
            user=cls.user,
            title="Other",
            content="Did Y",
            kind=K.achievement,
            job=cls.other_job,
        )
        cls.inactive_intro = ResumeSnippet.objects.create(
            user=cls.user, title="Off", content="z", kind=K.intro, is_active=False
        )

    def test_picks_one_intro_one_closing(self):
        sel = SnippetSelector(self._cv(), self.user.pk).select()
        self.assertEqual(sel["intro"], self.intro)
        self.assertEqual(sel["closing"], self.closing)

    def test_body_includes_kept_job_snippet_only(self):
        sel = SnippetSelector(self._cv(), self.user.pk).select()
        self.assertIn(self.body_kept, sel["body"])
        self.assertNotIn(self.body_other, sel["body"])

    def test_ordered_runs_intro_first_closing_last(self):
        sel = SnippetSelector(self._cv(), self.user.pk).select()
        self.assertEqual(sel["ordered"][0], self.intro)
        self.assertEqual(sel["ordered"][-1], self.closing)

    def test_inactive_snippet_excluded(self):
        sel = SnippetSelector(self._cv(), self.user.pk).select()
        self.assertNotEqual(sel["intro"], self.inactive_intro)


class SnippetSelectorLanguageTests(_CoverLetterCVMixin, TestCase):
    """The native-language tie-break orders an already-in-language snippet ahead of an
    equally-relevant one in the other language — but never resurrects a zero-relevance
    snippet just for matching the posting language."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("snip_tb")
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        K = ResumeSnippet.Kind
        # Two equally-relevant body snippets (both linked to the kept job): one DE, one EN.
        cls.body_de = ResumeSnippet.objects.create(
            user=cls.user,
            title="DE",
            content="Ich habe X gebaut.",
            kind=K.achievement,
            job=cls.job,
            language="de",
        )
        cls.body_en = ResumeSnippet.objects.create(
            user=cls.user,
            title="EN",
            content="I built X.",
            kind=K.achievement,
            job=cls.job,
            language="en",
        )
        # Relevant to nothing kept (no job/project/domain/skill link) → relevance score 0.
        cls.unlinked_de = ResumeSnippet.objects.create(
            user=cls.user,
            title="DEx",
            content="Nicht relevant.",
            kind=K.achievement,
            language="de",
        )

    def _body(self, posting_language):
        return SnippetSelector(
            self._cv(), self.user.pk, posting_language=posting_language
        ).select()["body"]

    def test_native_de_snippet_ordered_first_for_de_posting(self):
        self.assertEqual(self._body("de")[0], self.body_de)

    def test_native_en_snippet_ordered_first_for_en_posting(self):
        self.assertEqual(self._body("en")[0], self.body_en)

    def test_tie_break_never_resurrects_zero_relevance_snippet(self):
        # Posting is DE, but the irrelevant DE snippet (score 0) is still dropped while the
        # relevant EN snippet survives — language only breaks ties, it is not relevance.
        body = self._body("de")
        self.assertIn(self.body_en, body)
        self.assertNotIn(self.unlinked_de, body)


class SnippetSelectorEmbeddingTests(_CoverLetterCVMixin, TestCase):
    """Embedding-ranked selection (pipeline v2): cosine against the posting picks best
    intro + best closing + top-`max_body` body snippets on every grade; the alias chain
    (embed_alias → alias → "default") resolves the embedder; total embed failure degrades
    to the legacy structural scorer, never to an error."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("snip_embed")
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        K = ResumeSnippet.Kind
        cls.intro_close = ResumeSnippet.objects.create(
            user=cls.user, title="I1", content="Intro close", kind=K.intro
        )
        cls.intro_far = ResumeSnippet.objects.create(
            user=cls.user, title="I2", content="Intro far", kind=K.intro
        )
        cls.closing = ResumeSnippet.objects.create(
            user=cls.user, title="C", content="Closing text", kind=K.closing
        )
        # b1 is ALSO structurally linked to the kept job, so the fallback test can tell
        # the two paths apart: embeddings would drop b4; structure would drop b2..b4.
        cls.b1 = ResumeSnippet.objects.create(
            user=cls.user, title="B1", content="Body one", kind=K.achievement, job=cls.job
        )
        cls.b2 = ResumeSnippet.objects.create(
            user=cls.user, title="B2", content="Body two", kind=K.achievement
        )
        cls.b3 = ResumeSnippet.objects.create(
            user=cls.user, title="B3", content="Body three", kind=K.value_statement
        )
        cls.b4 = ResumeSnippet.objects.create(
            user=cls.user, title="B4", content="Body four", kind=K.achievement
        )
        cls.inactive = ResumeSnippet.objects.create(
            user=cls.user,
            title="Off",
            content="Inactive text",
            kind=K.achievement,
            is_active=False,
        )

    # Cosine ladder against the query [1, 0]: b1 > b2 > b3 >> b4; intro_close >> intro_far.
    _VECS = {
        "Intro close": [0.9, 0.1],
        "Intro far": [0.1, 0.9],
        "Closing text": [0.5, 0.5],
        "Body one": [0.95, 0.05],
        "Body two": [0.9, 0.2],
        "Body three": [0.8, 0.3],
        "Body four": [0.1, 0.9],
    }

    @classmethod
    def _fake_embed(cls, inputs, alias="default", user=None):
        # First input is the Instruct/Query prompt; the rest are snippet contents. A
        # content we never seeded (e.g. an inactive snippet's) raises KeyError = loud fail.
        return [[1.0, 0.0]] + [cls._VECS[text] for text in inputs[1:]]

    def _select(self, **kw):
        kw.setdefault("user", self.user)
        kw.setdefault("alias", "writer")
        kw.setdefault("posting_text", "We need builders.")
        return SnippetSelector(
            self._cv(), self.user.pk, posting_language="en", **kw
        ).select()

    def test_no_posting_text_skips_embedding(self):
        # Nothing to rank against -> structural path without a single embed call.
        with patch("jac.llm_prompts.embed") as m:
            sel = self._select(posting_text="")
        m.assert_not_called()
        self.assertEqual(sel["ranking"], "structural")

    def test_top_three_bodies_by_cosine_best_first(self):
        with patch("jac.llm_prompts.embed", side_effect=self._fake_embed):
            sel = self._select()
        self.assertEqual(sel["body"], [self.b1, self.b2, self.b3])
        self.assertEqual(sel["ranking"], "embedding")

    def test_intro_and_closing_are_best_of_kind(self):
        with patch("jac.llm_prompts.embed", side_effect=self._fake_embed):
            sel = self._select()
        self.assertEqual(sel["intro"], self.intro_close)
        self.assertEqual(sel["closing"], self.closing)
        self.assertEqual(sel["ordered"][0], self.intro_close)
        self.assertEqual(sel["ordered"][-1], self.closing)

    def test_inactive_snippets_never_embedded(self):
        seen: list[str] = []

        def spy(inputs, alias="default", user=None):
            seen.extend(inputs[1:])
            return self._fake_embed(inputs, alias=alias, user=user)

        with patch("jac.llm_prompts.embed", side_effect=spy):
            self._select()
        self.assertNotIn("Inactive text", seen)

    def test_native_language_breaks_cosine_ties(self):
        K = ResumeSnippet.Kind
        de = ResumeSnippet.objects.create(
            user=self.user, title="DE", content="Gleich", kind=K.achievement, language="de"
        )
        en = ResumeSnippet.objects.create(
            user=self.user, title="EN", content="Same", kind=K.achievement, language="en"
        )
        vecs = {**self._VECS, "Gleich": [0.99, 0.0], "Same": [0.99, 0.0]}

        def tie_embed(inputs, alias="default", user=None):
            return [[1.0, 0.0]] + [vecs[t] for t in inputs[1:]]

        with patch("jac.llm_prompts.embed", side_effect=tie_embed):
            body = SnippetSelector(
                self._cv(),
                self.user.pk,
                posting_language="de",
                posting_text="We need builders.",
                user=self.user,
            ).select()["body"]
        self.assertLess(body.index(de), body.index(en))

    def test_alias_chain_walks_to_default_then_succeeds(self):
        tried: list[str] = []

        def chain(inputs, alias="default", user=None):
            tried.append(alias)
            if alias != "default":
                raise NotImplementedError("no embed endpoint")
            return self._fake_embed(inputs, alias=alias, user=user)

        with patch("jac.llm_prompts.embed", side_effect=chain):
            sel = self._select(alias="anthropic-strong")
        self.assertEqual(tried, ["anthropic-strong", "default"])
        self.assertEqual(sel["ranking"], "embedding")

    def test_explicit_embed_alias_tried_first_and_stops_chain(self):
        tried: list[str] = []

        def spy(inputs, alias="default", user=None):
            tried.append(alias)
            return self._fake_embed(inputs, alias=alias, user=user)

        with patch("jac.llm_prompts.embed", side_effect=spy):
            self._select(alias="writer", embed_alias="embedder")
        self.assertEqual(tried, ["embedder"])

    def test_structural_fallback_when_every_alias_fails(self):
        # The mixin's setUp already makes embed raise for any alias. Legacy behaviour:
        # only the job-linked b1 scores > 0, everything unlinked is gated out.
        sel = self._select()
        self.assertEqual(sel["ranking"], "structural")
        self.assertEqual(sel["body"], [self.b1])


class SnippetSelectorMMRTests(_CoverLetterCVMixin, TestCase):
    """`[fullstack]-letter-quality`: the embedding body pick is MMR-diversified —
    relevance minus overlap with what is already picked — so two tellings of the same
    story can't both take a slot from a distinct third one. Intro/closing stay pure
    best-of-kind relevance; the structural fallback is untouched."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("snip_mmr")
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        K = ResumeSnippet.Kind
        cls.dup_a = ResumeSnippet.objects.create(
            user=cls.user, title="DupA", content="Billing story A", kind=K.achievement
        )
        cls.dup_b = ResumeSnippet.objects.create(
            user=cls.user, title="DupB", content="Billing story B", kind=K.achievement
        )
        cls.distinct = ResumeSnippet.objects.create(
            user=cls.user,
            title="Other",
            content="Mentoring story",
            kind=K.value_statement,
        )

    # dup_a and dup_b are near-identical vectors (mutual cosine ≈ 1.0) and the two most
    # posting-relevant; distinct trails on relevance but points somewhere else entirely.
    _VECS = {
        "Billing story A": [0.5, 0.866, 0.0],
        "Billing story B": [0.48, 0.877, 0.0],
        "Mentoring story": [0.45, 0.0, 0.893],
    }

    @classmethod
    def _fake_embed(cls, inputs, alias="default", user=None):
        return [[1.0, 0.0, 0.0]] + [cls._VECS[t] for t in inputs[1:]]

    def _select(self, max_body):
        with patch("jac.llm_prompts.embed", side_effect=self._fake_embed):
            return SnippetSelector(
                self._cv(),
                self.user.pk,
                max_body=max_body,
                posting_language="en",
                posting_text="We need billing.",
                user=self.user,
            ).select()

    def test_first_pick_stays_pure_relevance(self):
        self.assertEqual(self._select(max_body=2)["body"][0], self.dup_a)

    def test_near_duplicate_loses_its_slot_to_the_distinct_story(self):
        # Pure cosine top-2 would be [dup_a, dup_b]; MMR must swap in the distinct one.
        sel = self._select(max_body=2)
        self.assertEqual(sel["body"], [self.dup_a, self.distinct])
        self.assertEqual(sel["ranking"], "embedding")

    def test_room_for_all_keeps_all(self):
        # Diversity reorders — it never drops a snippet while slots remain.
        self.assertEqual(
            set(self._select(max_body=3)["body"]),
            {self.dup_a, self.dup_b, self.distinct},
        )


class CoverLetterBuildTests(_CoverLetterCVMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "cluser", email="me@example.com", first_name="Ada", last_name="Lovelace"
        )
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        K = ResumeSnippet.Kind
        ResumeSnippet.objects.create(
            user=cls.user, title="Intro", content="I build things.", kind=K.intro
        )
        ResumeSnippet.objects.create(
            user=cls.user,
            title="Achv",
            content="Shipped Y.",
            kind=K.achievement,
            job=cls.job,
        )
        ResumeSnippet.objects.create(
            user=cls.user, title="Bye", content="Thanks.", kind=K.closing
        )

    def test_build_uses_woven_body(self):
        with patch("jac.llm_prompts.complete", return_value="Woven letter body."):
            r = CoverLetter(
                self.user,
                self._jp(),
                self._cv(),
                address=JobPostAddress(company="Acme"),
            ).build()
        self.assertEqual(r["body"], "Woven letter body.")
        self.assertIn("Woven letter body.", r["text"])
        self.assertIn("Acme", r["text"])
        self.assertEqual(r["subject"], "Application for Backend Engineer")

    def test_falls_back_to_raw_snippets_when_llm_empty(self):
        with patch("jac.llm_prompts.complete", return_value=""):
            r = CoverLetter(
                self.user, self._jp(), self._cv(), address=JobPostAddress()
            ).build()
        self.assertIn("I build things.", r["body"])
        self.assertIn("Thanks.", r["body"])

    def test_salutation_named_when_contact_present(self):
        with patch("jac.llm_prompts.complete", return_value="x"):
            r = CoverLetter(
                self.user,
                self._jp(),
                self._cv(),
                address=JobPostAddress(contact_name="Jane Doe"),
            ).build()
        self.assertEqual(r["salutation"], "Dear Jane Doe,")

    def test_german_subject_and_generic_salutation(self):
        with patch("jac.llm_prompts.complete", return_value="x"):
            r = CoverLetter(
                self.user,
                self._jp(language="de"),
                self._cv(),
                address=JobPostAddress(),
            ).build()
        self.assertEqual(r["subject"], "Bewerbung als Backend Engineer")
        self.assertEqual(r["salutation"], "Sehr geehrte Damen und Herren,")

    def test_closing_is_language_furniture(self):
        """build() exposes `closing` so the SPA never hardcodes the bilingual strings."""
        with patch("jac.llm_prompts.complete", return_value="x"):
            en = CoverLetter(
                self.user, self._jp(), self._cv(), address=JobPostAddress()
            ).build()
            de = CoverLetter(
                self.user, self._jp(language="de"), self._cv(), address=JobPostAddress()
            ).build()
        self.assertEqual(en["closing"], "Kind regards,")
        self.assertEqual(de["closing"], "Mit freundlichen Grüßen,")

    def test_ai_share_present_and_in_range(self):
        with patch("jac.llm_prompts.complete", return_value="Body."):
            r = CoverLetter(
                self.user, self._jp(), self._cv(), address=JobPostAddress()
            ).build()
        self.assertIsInstance(r["ai_share"], float)
        self.assertGreaterEqual(r["ai_share"], 0.0)
        self.assertLessEqual(r["ai_share"], 1.0)

    def test_provenance_partitions_snippets_used(self):
        # All seeded snippets are EN; against a DE posting they are all "translated".
        with patch("jac.llm_prompts.complete", return_value="Body."):
            r = CoverLetter(
                self.user,
                self._jp(language="de"),
                self._cv(),
                address=JobPostAddress(),
            ).build()
        prov = r["snippet_provenance"]
        self.assertEqual(
            sorted(prov["native"] + prov["translated"]), sorted(r["snippets_used"])
        )
        self.assertEqual(prov["native"], [])
        self.assertEqual(r["ai_share"], 1.0)  # nothing authored in the posting language


class CoverLetterAiShareTests(_CoverLetterCVMixin, TestCase):
    """`CoverLetter._ai_share`: source provenance + a per-grade rewrite tax, clamped 0.0–1.0."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ai_share")
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )

    def _cl(self, grade="standard"):
        return CoverLetter(self.user, self._jp(title="x", posting_text="y"), self._cv(), grade=grade)

    def _snip(self, language, words=4):
        return ResumeSnippet(content=" ".join(["w"] * words), language=language)

    def test_all_native_light_is_just_the_rewrite_tax(self):
        self.assertEqual(
            self._cl("light")._ai_share([self._snip("en")], "en", False), 0.05
        )

    def test_all_native_strong_carries_more_tax(self):
        # Strong composes its own letter (pipeline v2), so its rewrite tax reflects
        # free composition, not polished stitching.
        self.assertEqual(
            self._cl("strong")._ai_share([self._snip("en")], "en", False), 0.6
        )

    def test_all_translated_is_fully_ai(self):
        self.assertEqual(
            self._cl("light")._ai_share([self._snip("en")], "de", False), 1.0
        )

    def test_no_snippets_is_fully_ai(self):
        self.assertEqual(self._cl()._ai_share([], "en", False), 1.0)

    def test_ai_fallback_is_fully_ai(self):
        self.assertEqual(self._cl()._ai_share([self._snip("en")], "en", True), 1.0)

    def test_mixed_lands_strictly_between(self):
        share = self._cl("light")._ai_share(
            [self._snip("de"), self._snip("en")], "de", False
        )
        self.assertGreater(share, 0.0)
        self.assertLess(share, 1.0)


class CoverLetterGroundingTests(_CoverLetterCVMixin, TestCase):
    """build() surfaces grounding only when asked, and never lies on the off/fallback paths."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "cl_ground", first_name="Ada", last_name="L"
        )
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        K = ResumeSnippet.Kind
        ResumeSnippet.objects.create(
            user=cls.user, title="Intro", content="I build things.", kind=K.intro
        )
        ResumeSnippet.objects.create(
            user=cls.user,
            title="Achv",
            content="Shipped Y.",
            kind=K.achievement,
            job=cls.job,
        )

    def _build(self, *, verify, complete_returns):
        with patch("jac.llm_prompts.complete", side_effect=complete_returns):
            return CoverLetter(
                self.user,
                self._jp(),
                self._cv(),
                address=JobPostAddress(company="Acme"),
                verify_grounding=verify,
            ).build()

    # Bodies below are ≥3 words on purpose: the snippets total 5 words, so anything
    # shorter would trip the standard shrinkage backstop and add a repair call.

    def test_grounding_not_checked_when_disabled(self):
        # Writer + critic (standard always reviews prose now); grounding stays the
        # 'not checked' sentinel because verify is off.
        r = self._build(
            verify=False, complete_returns=["A woven letter body.", "ISSUES 0"]
        )
        self.assertEqual(r["grounding"], {"count": None, "claims": []})

    def test_grounding_runs_and_surfaces_claims_when_enabled(self):
        # writer -> verifier -> critic; dirty grounding alone never repairs below
        # strong (flag-only, the v2 contract), so the claims just surface.
        r = self._build(
            verify=True,
            complete_returns=[
                "A woven letter body.",
                "UNSUPPORTED 1\n- Led a team of 10",
                "ISSUES 0",
            ],
        )
        self.assertEqual(r["grounding"]["count"], 1)
        self.assertEqual(r["grounding"]["claims"], ["Led a team of 10"])

    def test_grounding_clean_when_verifier_reports_zero(self):
        r = self._build(
            verify=True,
            complete_returns=["A woven letter body.", "UNSUPPORTED 0", "ISSUES 0"],
        )
        self.assertEqual(r["grounding"], {"count": 0, "claims": []})

    def test_raw_fallback_is_grounded_without_calling_verifier(self):
        # Writer returns '' -> raw snippet fallback; body IS the snippets, so count 0 and the
        # verifier is never called (only the one writer complete() is consumed).
        r = self._build(verify=True, complete_returns=[""])
        self.assertEqual(r["grounding"], {"count": 0, "claims": []})
        self.assertIn("I build things.", r["body"])


class CoverLetterStrongGroundingTests(_CoverLetterCVMixin, TestCase):
    """Pipeline v2: strong grade always audits grounding — `verify_grounding=False`
    notwithstanding — and gets exactly ONE repair pass when the audit finds unsupported
    claims. Standard keeps grounding strictly opt-in."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("cl_strong", first_name="Ada", last_name="L")
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        K = ResumeSnippet.Kind
        ResumeSnippet.objects.create(
            user=cls.user, title="Intro", content="I build things.", kind=K.intro
        )
        ResumeSnippet.objects.create(
            user=cls.user,
            title="Achv",
            content="Shipped Y.",
            kind=K.achievement,
            job=cls.job,
        )

    def _build(self, complete_returns, *, grade="strong", verify=False):
        with patch(
            "jac.llm_prompts.complete", side_effect=complete_returns
        ) as m:
            r = CoverLetter(
                self.user,
                self._jp(),
                self._cv(),
                address=JobPostAddress(company="Acme"),
                grade=grade,
                verify_grounding=verify,
            ).build()
        return r, m

    def test_strong_verifies_even_when_disabled(self):
        # Three completes: writer + verifier + critic — the audit runs although
        # verify=False, and strong prose is always reviewed.
        r, m = self._build(["Strong body.", "UNSUPPORTED 0", "ISSUES 0"])
        self.assertEqual(r["grounding"]["count"], 0)
        self.assertEqual(m.call_count, 3)

    def test_clean_audit_skips_the_repair_pass(self):
        r, m = self._build(["Strong body.", "UNSUPPORTED 0", "ISSUES 0"])
        self.assertEqual(r["body"], "Strong body.")
        self.assertFalse(r["grounding"]["repaired"])
        self.assertEqual(m.call_count, 3)

    def test_dirty_audit_triggers_one_repair_with_claims_fed_back(self):
        r, m = self._build(
            [
                "Draft one.",
                "UNSUPPORTED 1\n- Led a team of 10",
                "ISSUES 0",
                "Draft two.",
                "UNSUPPORTED 0",
            ]
        )
        self.assertEqual(r["body"], "Draft two.")
        self.assertEqual(r["grounding"]["count"], 0)
        self.assertTrue(r["grounding"]["repaired"])
        # The repair prompt (fourth complete) carries the offending claim back.
        repair_prompt = m.call_args_list[3].kwargs["prompt"]
        self.assertIn("Led a team of 10", repair_prompt)

    def test_surviving_claims_stay_flagged_no_second_repair(self):
        r, m = self._build(
            [
                "Draft one.",
                "UNSUPPORTED 1\n- claim A",
                "ISSUES 0",
                "Draft two.",
                "UNSUPPORTED 1\n- claim B",
            ]
        )
        self.assertEqual(r["body"], "Draft two.")
        self.assertEqual(r["grounding"]["count"], 1)
        self.assertEqual(r["grounding"]["claims"], ["claim B"])
        self.assertTrue(r["grounding"]["repaired"])
        self.assertEqual(m.call_count, 5)  # exactly one repair round

    def test_empty_repair_write_keeps_first_draft_and_its_audit(self):
        r, m = self._build(
            ["Draft one.", "UNSUPPORTED 1\n- claim A", "ISSUES 0", ""]
        )
        self.assertEqual(r["body"], "Draft one.")
        self.assertEqual(r["grounding"]["count"], 1)
        self.assertEqual(r["grounding"]["claims"], ["claim A"])
        self.assertFalse(r["grounding"]["repaired"])
        self.assertEqual(m.call_count, 4)  # failed repair write, no re-audit

    def test_standard_stays_opt_in(self):
        # Writer + critic; grounding keeps the exact legacy 'not checked' shape — no
        # `repaired` key leaks into non-strong grades.
        r, m = self._build(
            ["A fine woven body.", "ISSUES 0"], grade="standard", verify=False
        )
        self.assertEqual(r["grounding"], {"count": None, "claims": []})
        self.assertEqual(m.call_count, 2)


class CoverLetterCritiqueTests(_CoverLetterCVMixin, TestCase):
    """`[fullstack]-letter-quality`: the LetterCritic reviews standard+strong prose
    and its findings drive ONE repair rewrite; light stays glue-only. Standard also
    gets a deterministic shrinkage backstop — a "polish" far shorter than the snippets
    is a summary, whatever the critic says. Call order per grade:
    writer → [grounding] → critic → [repair → re-audit iff auditing]."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "cl_critic", first_name="Ada", last_name="L"
        )
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        K = ResumeSnippet.Kind
        ResumeSnippet.objects.create(
            user=cls.user, title="Intro", content="I build things.", kind=K.intro
        )
        ResumeSnippet.objects.create(
            user=cls.user,
            title="Achv",
            content="Shipped Y.",
            kind=K.achievement,
            job=cls.job,
        )

    # The snippets total 5 words, so the 0.6 shrinkage floor sits at 3: this 5-word
    # body is a legitimate polish, a 1-word body is a summary.
    _FULL = "A full five word body."

    def _build(self, complete_returns, *, grade="standard", verify=False):
        with patch("jac.llm_prompts.complete", side_effect=complete_returns) as m:
            r = CoverLetter(
                self.user,
                self._jp(),
                self._cv(),
                address=JobPostAddress(company="Acme"),
                grade=grade,
                verify_grounding=verify,
            ).build()
        return r, m

    def test_light_never_critiques(self):
        r, m = self._build(["Body."], grade="light")
        self.assertEqual(r["critique"], {"count": None, "claims": []})
        self.assertEqual(m.call_count, 1)  # writer only

    def test_standard_clean_critique_keeps_draft_one(self):
        r, m = self._build([self._FULL, "ISSUES 0"])
        self.assertEqual(r["body"], self._FULL)
        self.assertEqual(r["critique"], {"count": 0, "claims": []})
        self.assertEqual(m.call_count, 2)  # writer + critic, no repair

    def test_standard_dirty_critique_triggers_one_repair(self):
        r, m = self._build(
            [
                self._FULL,
                "ISSUES 1\n- the same story is told twice",
                "A better five word body.",
            ]
        )
        self.assertEqual(r["body"], "A better five word body.")
        self.assertEqual(r["critique"]["count"], 1)
        self.assertTrue(r["critique"]["repaired"])
        self.assertEqual(m.call_count, 3)
        # The repair prompt (third complete) carries the critic's note back.
        repair_prompt = m.call_args_list[2].kwargs["prompt"]
        self.assertIn("the same story is told twice", repair_prompt)

    def test_standard_grounding_stays_flag_only(self):
        # Opt-in grounding finds claims but the critic is clean: NO repair below
        # strong (the v2 contract) — the claims just stay flagged.
        r, m = self._build(
            [self._FULL, "UNSUPPORTED 1\n- Led a team of 10", "ISSUES 0"],
            verify=True,
        )
        self.assertEqual(r["body"], self._FULL)
        self.assertEqual(r["grounding"]["count"], 1)
        self.assertNotIn("repaired", r["critique"])
        self.assertEqual(m.call_count, 3)  # writer + verifier + critic

    def test_shrunk_standard_body_repairs_even_with_clean_critic(self):
        # 1 word vs a 3-word floor: the deterministic backstop fires although the
        # critic model saw nothing wrong.
        r, m = self._build(["Tiny.", "ISSUES 0", "A restored five word body."])
        self.assertEqual(r["body"], "A restored five word body.")
        self.assertEqual(r["critique"]["count"], 1)
        self.assertTrue(r["critique"]["repaired"])
        self.assertIn("full length", " ".join(r["critique"]["claims"]))

    def test_critic_failure_skips_repair_quietly(self):
        with _muted():
            r, m = self._build([self._FULL, RuntimeError("critic down")])
        self.assertEqual(r["body"], self._FULL)
        self.assertEqual(r["critique"], {"count": None, "claims": []})
        self.assertEqual(m.call_count, 2)

    def test_empty_repair_write_keeps_draft_one(self):
        r, m = self._build([self._FULL, "ISSUES 1\n- redundant middle", ""])
        self.assertEqual(r["body"], self._FULL)
        self.assertEqual(r["critique"]["count"], 1)
        self.assertFalse(r["critique"]["repaired"])
        self.assertEqual(m.call_count, 3)

    def test_strong_merges_critique_into_the_grounding_repair(self):
        # Dirty audit + dirty critique -> ONE rewrite carrying both channels, then
        # one grounding re-audit; both dicts flag repaired.
        r, m = self._build(
            [
                "Draft one.",
                "UNSUPPORTED 1\n- Led a team of 10",
                "ISSUES 1\n- intro retold in the closing",
                "Draft two.",
                "UNSUPPORTED 0",
            ],
            grade="strong",
        )
        self.assertEqual(r["body"], "Draft two.")
        self.assertEqual(r["grounding"]["count"], 0)
        self.assertTrue(r["grounding"]["repaired"])
        self.assertTrue(r["critique"]["repaired"])
        self.assertEqual(m.call_count, 5)
        repair_prompt = m.call_args_list[3].kwargs["prompt"]
        self.assertIn("Led a team of 10", repair_prompt)
        self.assertIn("intro retold in the closing", repair_prompt)

    def test_strong_clean_everything_skips_repair(self):
        r, m = self._build(
            ["Draft one.", "UNSUPPORTED 0", "ISSUES 0"], grade="strong"
        )
        self.assertEqual(r["body"], "Draft one.")
        self.assertFalse(r["grounding"]["repaired"])
        self.assertNotIn("repaired", r["critique"])
        self.assertEqual(m.call_count, 3)


# ===========================================================================
# Personal paragraph — the research step feeding the cover letter's slot
# ===========================================================================


class CompanyResearcherTests(TestCase):
    def test_ok_dossier_when_capable(self):
        with patch("jac.research.can_web_search", return_value=True), patch(
            "jac.research.web_search",
            return_value={"text": "Acme builds X.", "sources": ["https://a"]},
        ):
            out = CompanyResearcher("Acme", "posting", alias="strong").research()
        self.assertTrue(out["ok"])
        self.assertIn("Acme", out["dossier"])
        self.assertEqual(out["sources"], ["https://a"])

    def test_empty_company_skips(self):
        with patch("jac.research.can_web_search") as cap, patch(
            "jac.research.web_search"
        ) as ws:
            out = CompanyResearcher("", "posting").research()
        self.assertFalse(out["ok"])
        cap.assert_not_called()
        ws.assert_not_called()

    def test_non_capable_alias_skips_without_call(self):
        with patch("jac.research.can_web_search", return_value=False), patch(
            "jac.research.web_search"
        ) as ws:
            out = CompanyResearcher("Acme", "p", alias="default").research()
        self.assertFalse(out["ok"])
        ws.assert_not_called()

    def test_failure_returns_empty(self):
        with _muted(), patch("jac.research.can_web_search", return_value=True), patch(
            "jac.research.web_search", side_effect=RuntimeError("down")
        ):
            out = CompanyResearcher("Acme", "p").research()
        self.assertFalse(out["ok"])
        self.assertEqual(out["dossier"], "")

    def test_blank_text_is_not_ok(self):
        with patch("jac.research.can_web_search", return_value=True), patch(
            "jac.research.web_search", return_value={"text": "   ", "sources": []}
        ):
            out = CompanyResearcher("Acme", "p").research()
        self.assertFalse(out["ok"])


class CoverLetterPersonalParagraphTests(_CoverLetterCVMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "pp_user", email="me@example.com", first_name="Ada", last_name="Lovelace"
        )
        cls.job = Job.objects.create(
            user=cls.user, title="Dev", company="X", started=date(2020, 1, 1)
        )
        K = ResumeSnippet.Kind
        ResumeSnippet.objects.create(
            user=cls.user, title="Intro", content="I build things.", kind=K.intro
        )
        ResumeSnippet.objects.create(
            user=cls.user,
            title="Achv",
            content="Shipped Y.",
            kind=K.achievement,
            job=cls.job,
        )
        # A cached, fresh personality dossier so ensure_dossier() never calls the distiller here.
        from spa.models import PersonalityProfile

        prof = PersonalityProfile.objects.get(user=cls.user)
        prof.answers = {"values": "I value openness."}
        prof.answers_updated_at = timezone.now() - timedelta(hours=1)
        prof.dossier = "Ada values openness and craftsmanship."
        prof.dossier_built_at = timezone.now()
        prof.save()

    def _build(self, **kw):
        return CoverLetter(
            self.user,
            self._jp(),
            self._cv(),
            address=JobPostAddress(company="Acme"),
            **kw,
        ).build()

    def test_real_paragraph_when_capable(self):
        # Strong always audits and reviews: writer, audit, critic, paragraph writer.
        with patch("jac.research.can_web_search", return_value=True), patch(
            "jac.research.web_search",
            return_value={"text": "Acme builds rockets.", "sources": ["https://acme"]},
        ), patch(
            "jac.llm_prompts.complete",
            side_effect=[
                "Snippet body.",
                "UNSUPPORTED 0",
                "ISSUES 0",
                "I love Acme's mission.",
            ],
        ):
            r = self._build(grade="strong", personal_paragraph=True)
        self.assertFalse(r["personal_paragraph_is_stub"])
        self.assertEqual(r["personal_paragraph"], "I love Acme's mission.")
        self.assertEqual(r["personal_paragraph_sources"], ["https://acme"])
        self.assertIn("I love Acme's mission.", r["text"])

    def test_light_grade_always_stubs(self):
        with patch("jac.research.web_search") as ws, patch(
            "jac.llm_prompts.complete", return_value="Body."
        ):
            r = self._build(grade="light", personal_paragraph=True)
        self.assertTrue(r["personal_paragraph_is_stub"])
        self.assertEqual(r["personal_paragraph"], PERSONAL_STUB)
        self.assertIn(PERSONAL_STUB, r["text"])
        ws.assert_not_called()  # light never researches

    def test_non_capable_standard_stubs(self):
        with patch("jac.research.can_web_search", return_value=False), patch(
            "jac.research.web_search"
        ) as ws, patch("jac.llm_prompts.complete", return_value="Body."):
            r = self._build(grade="standard", personal_paragraph=True)
        self.assertTrue(r["personal_paragraph_is_stub"])
        ws.assert_not_called()  # capability pre-check short-circuits

    def test_not_requested_leaves_blank(self):
        with patch("jac.research.web_search") as ws, patch(
            "jac.llm_prompts.complete", return_value="Body."
        ):
            r = self._build(grade="strong", personal_paragraph=False)
        self.assertEqual(r["personal_paragraph"], "")
        self.assertFalse(r["personal_paragraph_is_stub"])
        ws.assert_not_called()

    def test_missing_personality_stubs_before_research(self):
        other = User.objects.create_user("pp_nopersona")
        with patch("jac.research.can_web_search", return_value=True), patch(
            "jac.research.web_search"
        ) as ws, patch("jac.llm_prompts.complete", return_value="Body."):
            r = CoverLetter(
                other,
                self._jp(),
                self._cv(),
                address=JobPostAddress(company="Acme"),
                grade="strong",
                personal_paragraph=True,
            ).build()
        self.assertTrue(r["personal_paragraph_is_stub"])
        ws.assert_not_called()  # personality checked first, no wasted search

    def test_stub_contributes_zero_to_ai_share(self):
        with patch("jac.llm_prompts.complete", return_value="Snippet body."):
            baseline = self._build(grade="light", personal_paragraph=False)["ai_share"]
        with patch("jac.research.web_search"), patch(
            "jac.llm_prompts.complete", return_value="Snippet body."
        ):
            stubbed = self._build(grade="light", personal_paragraph=True)
        self.assertTrue(stubbed["personal_paragraph_is_stub"])
        self.assertEqual(stubbed["ai_share"], baseline)

    def test_paragraph_excluded_from_snippet_grounding(self):
        with patch("jac.research.can_web_search", return_value=True), patch(
            "jac.research.web_search",
            return_value={"text": "Acme builds rockets.", "sources": []},
        ), patch("jac.cover_letter.FaithfulnessCheck") as FC, patch(
            "jac.llm_prompts.complete",
            # writer -> (FC is mocked, no call) -> critic -> paragraph writer -> its
            # own grounding check.
            side_effect=[
                "Snippet body.",
                "ISSUES 0",
                "I love Acme rockets.",
                "UNSUPPORTED 0",
            ],
        ):
            FC.return_value.critique.return_value = {"count": 0, "claims": []}
            r = self._build(
                grade="strong", personal_paragraph=True, verify_grounding=True
            )
        body_arg = (
            FC.call_args.args[0] if FC.call_args.args else FC.call_args.kwargs["body"]
        )
        self.assertIn("Snippet body.", body_arg)
        self.assertNotIn("I love Acme rockets.", body_arg)
        self.assertEqual(r["personal_paragraph"], "I love Acme rockets.")


class EditableBodyTests(TestCase):
    """editable_body(): the body-only slice that lands in JobApplication.cover_letter —
    body + personal paragraph (real or stub), never the furnished full text."""

    def test_body_only_when_no_personal_paragraph(self):
        self.assertEqual(editable_body({"body": "Hi.", "personal_paragraph": ""}), "Hi.")

    def test_appends_personal_paragraph_as_own_block(self):
        letter = {"body": "Hi.", "personal_paragraph": "I admire ACME."}
        self.assertEqual(editable_body(letter), "Hi.\n\nI admire ACME.")

    def test_stub_paragraph_stays_loud(self):
        letter = {"body": "Hi.", "personal_paragraph": PERSONAL_STUB}
        self.assertIn(PERSONAL_STUB, editable_body(letter))
