"""Personality questionnaire test suite (spa).

Covers the PersonalityProfile model (auto-creation, staleness, cached distillation),
the PersonalityQuestion model + CRUD API (system defaults vs. user-added), the
PersonalityDistiller (DB-resolved labels), and the personality API (read/patch +
force-rebuild).

All LLM I/O is mocked — no live calls. Wrap only the deliberate error-path tests in _muted().
The research/paragraph side lives in jac/tests_scraper.py.
"""

import logging
from contextlib import contextmanager
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from spa.distill import PersonalityDistiller
from spa.models import PersonalityProfile, PersonalityQuestion
from spa.personality_questions import MAX_ANSWER_LEN, PERSONALITY_QUESTIONS


@contextmanager
def _muted():
    """Silence logging inside the block — wrap ONLY the deliberate LLM error-path tests."""
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def _system_user():
    return User.objects.get_or_create(
        username=settings.SYSTEM_USER_USERNAME, defaults={"is_active": False}
    )[0]


_PROMPTS = {q["slug"]: q["prompt"] for q in PERSONALITY_QUESTIONS}


def _seed_system_questions(*slugs):
    """Create system-default PersonalityQuestions (what seed_system_defaults would).
    With no args, seeds the whole default pool; otherwise just the named slugs."""
    system = _system_user()
    chosen = slugs or [q["slug"] for q in PERSONALITY_QUESTIONS]
    rows = []
    for i, slug in enumerate(chosen):
        rows.append(
            PersonalityQuestion.objects.create(
                user=system, slug=slug, prompt=_PROMPTS.get(slug, slug), order=i
            )
        )
    return rows


# ===========================================================================
# PersonalityProfile model
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


# ===========================================================================
# PersonalityQuestion model
# ===========================================================================


class PersonalityQuestionModelTests(TestCase):
    def test_for_user_returns_system_and_own_only(self):
        system = _system_user()
        me = User.objects.create_user("me")
        other = User.objects.create_user("other")
        sys_q = PersonalityQuestion.objects.create(
            user=system, slug="flow", prompt="Flow?"
        )
        mine = PersonalityQuestion.objects.create(user=me, slug="mine", prompt="Mine?")
        theirs = PersonalityQuestion.objects.create(
            user=other, slug="mine", prompt="Theirs?"
        )
        visible = set(PersonalityQuestion.objects.for_user(me))
        self.assertEqual(visible, {sys_q, mine})
        self.assertNotIn(theirs, visible)

    def test_defaults_returns_only_system_rows(self):
        system = _system_user()
        me = User.objects.create_user("me2")
        sys_q = PersonalityQuestion.objects.create(
            user=system, slug="flow", prompt="Flow?"
        )
        PersonalityQuestion.objects.create(user=me, slug="mine", prompt="Mine?")
        self.assertEqual(list(PersonalityQuestion.objects.defaults()), [sys_q])

    def test_slug_unique_per_user(self):
        me = User.objects.create_user("dup")
        PersonalityQuestion.objects.create(user=me, slug="flow", prompt="Flow?")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonalityQuestion.objects.create(user=me, slug="flow", prompt="Again?")


# ===========================================================================
# ensure_dossier (feeds DB-resolved labels to the distiller)
# ===========================================================================


class EnsureDossierTests(TestCase):
    def setUp(self):
        self.prof = PersonalityProfile.objects.get(user=User.objects.create_user("ed"))
        self.prof.answers = {"values": "openness"}
        self.prof.answers_updated_at = timezone.now()
        self.prof.save()

    # ensure_dossier takes the run's executor since the single-executor rework;
    # `spa.distill.complete` is patched, so a sentinel object suffices here.

    def test_builds_and_caches(self):
        with patch("spa.distill.complete", return_value="Dossier text.") as m:
            d1 = self.prof.ensure_dossier(object())
            d2 = self.prof.ensure_dossier(object())
        self.assertEqual(d1, "Dossier text.")
        self.assertEqual(d2, "Dossier text.")
        m.assert_called_once()  # second call serves the cache

    def test_rebuilds_when_answers_change(self):
        with patch("spa.distill.complete", return_value="v1"):
            self.prof.ensure_dossier(object())
        self.prof.answers = {"values": "craftsmanship"}
        self.prof.answers_updated_at = timezone.now() + timezone.timedelta(seconds=1)
        self.prof.save()
        with patch("spa.distill.complete", return_value="v2") as m:
            d = self.prof.ensure_dossier(object())
        self.assertEqual(d, "v2")
        m.assert_called_once()

    def test_no_answers_returns_empty_without_call(self):
        prof = PersonalityProfile.objects.get(user=User.objects.create_user("noans"))
        with patch("spa.distill.complete") as m:
            self.assertEqual(prof.ensure_dossier(object()), "")
        m.assert_not_called()

    def test_feeds_db_resolved_labels_into_the_prompt(self):
        """A question's real prompt (system or user-added) must reach the distiller, not
        the bare slug — that's the whole point of moving questions into the DB."""
        _seed_system_questions("flow")  # prompt = the real 'What do you lose track…'
        prof = PersonalityProfile.objects.get(user=User.objects.create_user("labels"))
        prof.answers = {"flow": "writing code"}
        prof.answers_updated_at = timezone.now()
        prof.save()
        with patch("spa.distill.complete", return_value="D") as m:
            prof.ensure_dossier(object())
        prompt = m.call_args.kwargs["prompt"]
        self.assertIn(_PROMPTS["flow"], prompt)
        self.assertIn("writing code", prompt)


# ===========================================================================
# Distiller
# ===========================================================================


class PersonalityDistillerTests(TestCase):
    # The distiller takes the run's executor since the single-executor rework;
    # `spa.distill.complete` is patched, so a sentinel object suffices here.

    def test_empty_answers_skips_llm(self):
        with patch("spa.distill.complete") as m:
            self.assertEqual(PersonalityDistiller({}, executor=object()).distill(), "")
            self.assertEqual(
                PersonalityDistiller({"values": ""}, executor=object()).distill(), ""
            )
        m.assert_not_called()

    def test_returns_stripped_prose(self):
        with patch("spa.distill.complete", return_value="  A sketch.  ") as m:
            out = PersonalityDistiller(
                {"values": "openness"}, executor=object()
            ).distill()
        self.assertEqual(out, "A sketch.")
        m.assert_called_once()

    def test_failure_returns_empty(self):
        with _muted(), patch("spa.distill.complete", side_effect=RuntimeError("x")):
            self.assertEqual(
                PersonalityDistiller({"values": "openness"}, executor=object()).distill(),
                "",
            )

    def test_labels_resolve_the_prompt(self):
        with patch("spa.distill.complete", return_value="D") as m:
            PersonalityDistiller(
                {"q1": "an answer"}, labels={"q1": "A real question?"},
                executor=object(),
            ).distill()
        prompt = m.call_args.kwargs["prompt"]
        self.assertIn("A real question?", prompt)
        self.assertIn("an answer", prompt)

    def test_missing_label_falls_back_to_slug(self):
        with patch("spa.distill.complete", return_value="D") as m:
            PersonalityDistiller(
                {"orphan": "an answer"}, labels={}, executor=object()
            ).distill()
        self.assertIn("orphan", m.call_args.kwargs["prompt"])


# ===========================================================================
# Personality API (answers + dossier)
# ===========================================================================


class PersonalityAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("api")
        self.client.force_authenticate(self.user)

    def test_get_includes_seeded_questions(self):
        _seed_system_questions("flow", "admire")
        r = self.client.get("/api/spa/personality/")
        self.assertEqual(r.status_code, 200)
        slugs = [q["slug"] for q in r.data["questions"]]
        self.assertEqual(slugs, ["flow", "admire"])
        first = r.data["questions"][0]
        self.assertEqual(set(first), {"pk", "slug", "prompt", "editable"})
        self.assertFalse(first["editable"])  # system defaults are read-only

    def test_patch_updates_answers_and_stamps(self):
        r = self.client.patch(
            "/api/spa/personality/", {"answers": {"values": "x"}}, format="json"
        )
        self.assertEqual(r.status_code, 200)
        prof = PersonalityProfile.objects.get(user=self.user)
        self.assertEqual(prof.answers, {"values": "x"})
        self.assertIsNotNone(prof.answers_updated_at)

    def test_answer_over_cap_rejected(self):
        r = self.client.patch(
            "/api/spa/personality/",
            {"answers": {"flow": "x" * (MAX_ANSWER_LEN + 1)}},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_blank_answers_dropped(self):
        r = self.client.patch(
            "/api/spa/personality/",
            {"answers": {"flow": "  ", "childhood": "an astronaut"}},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        prof = PersonalityProfile.objects.get(user=self.user)
        self.assertEqual(prof.answers, {"childhood": "an astronaut"})

    def test_dossier_is_read_only(self):
        self.client.patch("/api/spa/personality/", {"dossier": "hacked"}, format="json")
        prof = PersonalityProfile.objects.get(user=self.user)
        self.assertNotEqual(prof.dossier, "hacked")

    # The rebuild view resolves an executor from optional body {provider, model}
    # ([fullstack]-llm-config-rework repair 3); `spa.views.resolve_executor` is the
    # patch target — red until the view imports it.

    def test_rebuild_endpoint_force_distils(self):
        prof = PersonalityProfile.objects.get(user=self.user)
        prof.answers = {"values": "x"}
        prof.answers_updated_at = timezone.now()
        prof.save()
        with (
            patch("spa.views.resolve_executor", return_value=object()),
            patch("spa.distill.complete", return_value="Fresh dossier."),
        ):
            r = self.client.post("/api/spa/personality/rebuild/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["dossier"], "Fresh dossier.")

    def test_rebuild_maps_executor_error_to_400(self):
        from llm_connector.conf import ExecutorError

        with patch(
            "spa.views.resolve_executor",
            side_effect=ExecutorError("No executor available."),
        ):
            r = self.client.post("/api/spa/personality/rebuild/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("provider", r.data)


# ===========================================================================
# PersonalityQuestion CRUD API
# ===========================================================================


class PersonalityQuestionAPITests(APITestCase):
    LIST = "/api/spa/personality/questions/"

    def setUp(self):
        self.user = User.objects.create_user("qapi")
        self.client.force_authenticate(self.user)

    def _detail(self, pk):
        return f"{self.LIST}{pk}/"

    def test_list_returns_system_plus_own_editable_flags(self):
        _seed_system_questions("flow")
        mine = PersonalityQuestion.objects.create(
            user=self.user, slug="mine", prompt="Mine?", order=99
        )
        r = self.client.get(self.LIST)
        self.assertEqual(r.status_code, 200)
        by_slug = {q["slug"]: q for q in r.data}
        self.assertFalse(by_slug["flow"]["editable"])
        self.assertTrue(by_slug["mine"]["editable"])
        self.assertEqual(by_slug["mine"]["pk"], mine.pk)

    def test_embedded_questions_are_system_first(self):
        _seed_system_questions("flow")
        PersonalityQuestion.objects.create(
            user=self.user, slug="mine", prompt="Mine?", order=0
        )
        r = self.client.get("/api/spa/personality/")
        slugs = [q["slug"] for q in r.data["questions"]]
        self.assertEqual(slugs, ["flow", "mine"])  # system default before own

    def test_create_owns_the_question(self):
        r = self.client.post(self.LIST, {"prompt": "What drives you?"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.data["editable"])
        self.assertTrue(r.data["slug"])
        q = PersonalityQuestion.objects.get(pk=r.data["pk"])
        self.assertEqual(q.user, self.user)

    def test_create_dedupes_slug_against_system_default(self):
        _seed_system_questions("flow")  # slug 'flow' already taken by the system row
        r = self.client.post(self.LIST, {"prompt": "Flow"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["slug"], "flow-2")  # never shadows the default's key

    def test_create_blank_prompt_400(self):
        r = self.client.post(self.LIST, {"prompt": "   "}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_delete_own_question(self):
        q = PersonalityQuestion.objects.create(
            user=self.user, slug="mine", prompt="Mine?"
        )
        r = self.client.delete(self._detail(q.pk))
        self.assertEqual(r.status_code, 204)
        self.assertFalse(PersonalityQuestion.objects.filter(pk=q.pk).exists())

    def test_cannot_delete_system_default(self):
        (sys_q,) = _seed_system_questions("flow")
        r = self.client.delete(self._detail(sys_q.pk))
        self.assertEqual(r.status_code, 404)
        self.assertTrue(PersonalityQuestion.objects.filter(pk=sys_q.pk).exists())

    def test_cannot_patch_system_default(self):
        (sys_q,) = _seed_system_questions("flow")
        r = self.client.patch(
            self._detail(sys_q.pk), {"prompt": "hijacked"}, format="json"
        )
        self.assertEqual(r.status_code, 404)
        sys_q.refresh_from_db()
        self.assertNotEqual(sys_q.prompt, "hijacked")

    def test_cannot_touch_another_users_question(self):
        other = User.objects.create_user("stranger")
        theirs = PersonalityQuestion.objects.create(
            user=other, slug="theirs", prompt="Theirs?"
        )
        r = self.client.delete(self._detail(theirs.pk))
        self.assertEqual(r.status_code, 404)
