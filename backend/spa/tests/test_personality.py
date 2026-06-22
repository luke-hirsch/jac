"""Personality questionnaire test suite (spa).

Covers the PersonalityProfile model (auto-creation, staleness, cached distillation),
the PersonalityDistiller, and the personality API (read/patch + force-rebuild).

All LLM I/O is mocked — no live calls. Wrap only the deliberate error-path tests in _muted().
The research/paragraph side lives in jac/tests_scraper.py.
"""

import logging
from contextlib import contextmanager
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from spa.distill import PersonalityDistiller
from spa.models import PERSONALITY_QUESTIONS, PersonalityProfile


@contextmanager
def _muted():
    """Silence logging inside the block — wrap ONLY the deliberate LLM error-path tests."""
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


# ===========================================================================
# Model
# ===========================================================================


class PersonalityProfileModelTests(TestCase):
    def test_auto_created_on_user_creation(self):
        user = User.objects.create_user("auto")
        self.assertTrue(PersonalityProfile.objects.filter(user=user).exists())

    def test_has_answers(self):
        prof = PersonalityProfile.objects.get(user=User.objects.create_user("ha"))
        self.assertFalse(prof.has_answers())
        prof.answers = {"values": "openness"}
        self.assertTrue(prof.has_answers())
        prof.answers = {"values": ""}
        self.assertFalse(prof.has_answers())

    def test_dossier_stale(self):
        prof = PersonalityProfile.objects.get(user=User.objects.create_user("stale"))
        self.assertTrue(prof.dossier_stale())  # never built
        prof.dossier_built_at = timezone.now()
        prof.answers_updated_at = timezone.now() - timezone.timedelta(hours=1)
        self.assertFalse(prof.dossier_stale())  # answers older than build
        prof.answers_updated_at = timezone.now() + timezone.timedelta(hours=1)
        self.assertTrue(prof.dossier_stale())  # answers changed after build


class EnsureDossierTests(TestCase):
    def setUp(self):
        self.prof = PersonalityProfile.objects.get(
            user=User.objects.create_user("ed")
        )
        self.prof.answers = {"values": "openness"}
        self.prof.answers_updated_at = timezone.now()
        self.prof.save()

    def test_builds_and_caches(self):
        with patch("spa.distill.complete", return_value="Dossier text.") as m:
            d1 = self.prof.ensure_dossier()
            d2 = self.prof.ensure_dossier()
        self.assertEqual(d1, "Dossier text.")
        self.assertEqual(d2, "Dossier text.")
        m.assert_called_once()  # second call serves the cache

    def test_rebuilds_when_answers_change(self):
        with patch("spa.distill.complete", return_value="v1"):
            self.prof.ensure_dossier()
        self.prof.answers = {"values": "craftsmanship"}
        self.prof.answers_updated_at = timezone.now() + timezone.timedelta(seconds=1)
        self.prof.save()
        with patch("spa.distill.complete", return_value="v2") as m:
            d = self.prof.ensure_dossier()
        self.assertEqual(d, "v2")
        m.assert_called_once()

    def test_no_answers_returns_empty_without_call(self):
        prof = PersonalityProfile.objects.get(user=User.objects.create_user("noans"))
        with patch("spa.distill.complete") as m:
            self.assertEqual(prof.ensure_dossier(), "")
        m.assert_not_called()


# ===========================================================================
# Distiller
# ===========================================================================


class PersonalityDistillerTests(TestCase):
    def test_empty_answers_skips_llm(self):
        with patch("spa.distill.complete") as m:
            self.assertEqual(PersonalityDistiller({}).distill(), "")
            self.assertEqual(PersonalityDistiller({"values": ""}).distill(), "")
        m.assert_not_called()

    def test_returns_stripped_prose(self):
        with patch("spa.distill.complete", return_value="  A sketch.  ") as m:
            out = PersonalityDistiller({"values": "openness"}).distill()
        self.assertEqual(out, "A sketch.")
        m.assert_called_once()

    def test_failure_returns_empty(self):
        with _muted(), patch("spa.distill.complete", side_effect=RuntimeError("x")):
            self.assertEqual(
                PersonalityDistiller({"values": "openness"}).distill(), ""
            )


# ===========================================================================
# API
# ===========================================================================


class PersonalityAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("api")
        self.client.force_authenticate(self.user)

    def test_get_includes_questions(self):
        r = self.client.get("/api/spa/personality/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["questions"], PERSONALITY_QUESTIONS)

    def test_patch_updates_answers_and_stamps(self):
        r = self.client.patch(
            "/api/spa/personality/", {"answers": {"values": "x"}}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        prof = PersonalityProfile.objects.get(user=self.user)
        self.assertEqual(prof.answers, {"values": "x"})
        self.assertIsNotNone(prof.answers_updated_at)

    def test_dossier_is_read_only(self):
        self.client.patch(
            "/api/spa/personality/", {"dossier": "hacked"}, format="json"
        )
        prof = PersonalityProfile.objects.get(user=self.user)
        self.assertNotEqual(prof.dossier, "hacked")

    def test_rebuild_endpoint_force_distils(self):
        prof = PersonalityProfile.objects.get(user=self.user)
        prof.answers = {"values": "x"}
        prof.answers_updated_at = timezone.now()
        prof.save()
        with patch("spa.distill.complete", return_value="Fresh dossier."):
            r = self.client.post("/api/spa/personality/rebuild/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["dossier"], "Fresh dossier.")
