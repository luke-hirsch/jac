"""Eval tooling + management commands (cv_eval _resolve_runs, cv smoke)."""

import io
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from jac.management.commands.cv_eval import _resolve_runs
from jac.models import ApplicationLayout, Domain, Job, Skill

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


class SeedDefaultsTests(TestCase):
    """seed_default_domains: system domains + the default ApplicationLayout (idempotent)."""

    def test_seeds_domains_and_default_layout_with_template(self):
        with tempfile.TemporaryDirectory() as media:
            with override_settings(MEDIA_ROOT=media):
                call_command("seed_default_domains", stdout=io.StringIO())
                system = User.objects.get(username=settings.SYSTEM_USER_USERNAME)
                self.assertTrue(Domain.objects.filter(user=system).exists())
                layout = ApplicationLayout.objects.get(user=system, name="default")
                self.assertTrue(layout.template)
                with layout.template.open() as fh:
                    spec = json.load(fh)
                self.assertIn("cv", spec)

                # Re-run: no duplicate layout, template stays attached once.
                call_command("seed_default_domains", stdout=io.StringIO())
                self.assertEqual(
                    ApplicationLayout.objects.filter(user=system).count(), 1
                )
