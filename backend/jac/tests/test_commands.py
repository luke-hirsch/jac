"""Eval tooling + management commands (cv_eval _resolve_runs, cv smoke)."""

import io
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from jac.management.commands.cv_eval import _resolve_runs
from jac.models import ApplicationLayout, Domain, Job, Skill
from spa.models import PersonalityQuestion
from spa.personality_questions import PERSONALITY_QUESTIONS

from ._helpers import _muted, _keep_all


class ResolveRunsTests(TestCase):
    """cv_eval._resolve_runs: the grade×llm selection matrix."""

    def _strength(self, alias):
        return {"default": "light", "reasoning": "standard"}.get(alias, "strong")

    def test_neither_uses_default_at_autodetected_grade(self):
        self.assertEqual(
            _resolve_runs(None, None, ["a", "b"], self._strength),
            [("default", "light")],
        )

    def test_llm_only_autodetects_grade(self):
        self.assertEqual(
            _resolve_runs(None, "reasoning", ["a", "b"], self._strength),
            [("reasoning", "standard")],
        )

    def test_grade_only_fans_out_over_all_models(self):
        self.assertEqual(
            _resolve_runs("standard", None, ["a", "b"], self._strength),
            [("a", "standard"), ("b", "standard")],
        )

    def test_both_uses_the_exact_pair(self):
        self.assertEqual(
            _resolve_runs("light", "reasoning", ["a", "b"], self._strength),
            [("reasoning", "light")],
        )


class CVCommandSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username="cmduser")
        cls.skill = Skill.objects.create(user=cls.user, name="Python")
        cls.job = Job.objects.create(
            user=cls.user, title="Eng", company="Acme", started=date(2022, 1, 1)
        )
        cls.job.skills.add(cls.skill)

    @patch("jac.cv.CV.filter_cv", new=_keep_all)
    def test_cv_test_writes_one_md_per_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            call_command(
                "cv_test",
                "--user",
                str(self.user.pk),
                "--job",
                "Senior Python engineer",
                "--grades",
                "light",
                "standard",
                "--out-dir",
                tmp,
                stdout=io.StringIO(),
            )
            self.assertTrue((Path(tmp) / "cv_light.md").exists())
            self.assertTrue((Path(tmp) / "cv_standard.md").exists())

    @patch("jac.cv.CV.filter_cv", new=_keep_all)
    def test_cv_eval_writes_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "posting.md"
            job.write_text("Senior Python engineer")
            # The eval user has no LLMConfig, so resolution logs an expected
            # 'falling back to settings' warning — mute it.
            with _muted():
                call_command(
                    "cv_eval",
                    "--user",
                    str(self.user.pk),
                    "--job-file",
                    str(job),
                    "--out-dir",
                    tmp,
                    stdout=io.StringIO(),
                )
            self.assertTrue((Path(tmp) / "findings.json").exists())
            self.assertTrue((Path(tmp) / "findings.md").exists())


class SeedSystemDefaultsTests(TestCase):
    """seed_system_defaults (renamed from seed_default_domains): system domains, the
    personality-question pool, and BOTH system layouts ("default" one-page, "two-page"),
    idempotent, and refreshing a stored template when the resource changed."""

    def test_seeds_domains_questions_and_both_layouts_with_templates(self):
        with tempfile.TemporaryDirectory() as media:
            with override_settings(MEDIA_ROOT=media):
                call_command("seed_system_defaults", stdout=io.StringIO())
                system = User.objects.get(username=settings.SYSTEM_USER_USERNAME)
                self.assertTrue(Domain.objects.filter(user=system).exists())

                # The whole default question pool is seeded, system-owned, by slug.
                seeded = set(
                    PersonalityQuestion.objects.filter(user=system).values_list(
                        "slug", flat=True
                    )
                )
                self.assertEqual(
                    seeded, {q["slug"] for q in PERSONALITY_QUESTIONS}
                )

                one = ApplicationLayout.objects.get(user=system, name="default")
                self.assertTrue(one.template)
                with one.template.open() as fh:
                    spec = json.load(fh)
                self.assertEqual(spec["cv"]["pages"], 1)
                # Section keys match the cv_content contract (plural), not legacy singular.
                self.assertIn("educations", spec["cv"]["sections"])
                self.assertNotIn("education", spec["cv"]["sections"])

                two = ApplicationLayout.objects.get(user=system, name="two-page")
                self.assertTrue(two.template)
                with two.template.open() as fh:
                    spec2 = json.load(fh)
                self.assertEqual(spec2["cv"]["pages"], 2)

                # Re-run: idempotent — still exactly the two system layouts and one
                # question per slug (no duplicates).
                call_command("seed_system_defaults", stdout=io.StringIO())
                self.assertEqual(
                    ApplicationLayout.objects.filter(user=system).count(), 2
                )
                self.assertEqual(
                    PersonalityQuestion.objects.filter(user=system).count(),
                    len(PERSONALITY_QUESTIONS),
                )

    def test_prune_drops_stale_system_question(self):
        with tempfile.TemporaryDirectory() as media:
            with override_settings(MEDIA_ROOT=media):
                system, _ = User.objects.get_or_create(
                    username=settings.SYSTEM_USER_USERNAME,
                    defaults={"is_active": False},
                )
                PersonalityQuestion.objects.create(
                    user=system, slug="retired", prompt="An old question?"
                )
                call_command("seed_system_defaults", "--prune", stdout=io.StringIO())
                self.assertFalse(
                    PersonalityQuestion.objects.filter(
                        user=system, slug="retired"
                    ).exists()
                )
                # A current default is created, not pruned.
                self.assertTrue(
                    PersonalityQuestion.objects.filter(
                        user=system, slug=PERSONALITY_QUESTIONS[0]["slug"]
                    ).exists()
                )

    def test_seed_refreshes_stale_template(self):
        with tempfile.TemporaryDirectory() as media:
            with override_settings(MEDIA_ROOT=media):
                system, _ = User.objects.get_or_create(
                    username=settings.SYSTEM_USER_USERNAME,
                    defaults={"is_active": False},
                )
                layout = ApplicationLayout.objects.create(user=system, name="default")
                layout.template.save(
                    "default_layout.json", ContentFile(b'{"stale": true}')
                )

                call_command("seed_system_defaults", stdout=io.StringIO())

                layout.refresh_from_db()
                with layout.template.open() as fh:
                    spec = json.load(fh)
                self.assertNotIn("stale", spec)
                self.assertIn("cv", spec)
