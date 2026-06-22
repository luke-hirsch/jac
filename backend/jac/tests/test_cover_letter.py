"""Cover-letter assembly — snippet selection, build, ai_share, grounding."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from jac.cover_letter import CoverLetter, SnippetSelector
from jac.models import Domain, Job, JobPostAddress, ResumeSnippet

from ._helpers import _CoverLetterCVMixin


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
        self.assertEqual(
            self._cl("strong")._ai_share([self._snip("en")], "en", False), 0.45
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

    def test_grounding_not_checked_when_disabled(self):
        # Only the writer call happens; grounding is the 'not checked' sentinel.
        r = self._build(verify=False, complete_returns=["Woven body."])
        self.assertEqual(r["grounding"], {"count": None, "claims": []})

    def test_grounding_runs_and_surfaces_claims_when_enabled(self):
        # First complete() -> writer body; second -> the verifier reply.
        r = self._build(
            verify=True,
            complete_returns=["Woven body.", "UNSUPPORTED 1\n- Led a team of 10"],
        )
        self.assertEqual(r["grounding"]["count"], 1)
        self.assertEqual(r["grounding"]["claims"], ["Led a team of 10"])

    def test_grounding_clean_when_verifier_reports_zero(self):
        r = self._build(verify=True, complete_returns=["Woven body.", "UNSUPPORTED 0"])
        self.assertEqual(r["grounding"], {"count": 0, "claims": []})

    def test_raw_fallback_is_grounded_without_calling_verifier(self):
        # Writer returns '' -> raw snippet fallback; body IS the snippets, so count 0 and the
        # verifier is never called (only the one writer complete() is consumed).
        r = self._build(verify=True, complete_returns=[""])
        self.assertEqual(r["grounding"], {"count": 0, "claims": []})
        self.assertIn("I build things.", r["body"])
