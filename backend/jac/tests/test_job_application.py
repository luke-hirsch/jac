"""JobApplication + ApplicationLayout — model defaults + REST CRUD/scoping.

Structure: `JobPosting 1──< JobApplication 1──< GenerationRun` (`runs`). The application is
the user-editable artefact (tailored CV JSON + cover letter) at `/api/jac/applications/`;
layouts at `/api/jac/layouts/` follow the Domain pattern (system defaults readable by all,
writes own-only). The layout FK is non-null with a callable default resolving to the system
"default" layout — also the SET_DEFAULT target when a custom layout is deleted.
"""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APITestCase

from jac.models import (
    ApplicationLayout,
    GenerationRun,
    JobApplication,
    JobPostAddress,
    JobPosting,
    default_application_layout,
)

from ._helpers import _application


class JobApplicationModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="app_model", password="pass")
        cls.posting = JobPosting.objects.create(user=cls.user, posting_text="x")

    def test_defaults(self):
        app = JobApplication.objects.create(user=self.user, posting=self.posting)
        self.assertEqual(app.status, "draft")
        self.assertEqual(app.cv_content, {})
        self.assertEqual(app.cover_letter, "")
        self.assertEqual(app.layout.name, "default")
        self.assertEqual(app.layout.user.username, settings.SYSTEM_USER_USERNAME)

    def test_default_layout_is_created_once(self):
        first = default_application_layout()
        second = default_application_layout()
        self.assertEqual(first, second)
        self.assertEqual(
            ApplicationLayout.objects.filter(name="default").count(), 1
        )

    def test_clean_flips_status_when_posting_inactive(self):
        app = JobApplication.objects.create(user=self.user, posting=self.posting)
        self.posting.active = False
        self.posting.save(update_fields=["active"])
        app.refresh_from_db()
        app.clean()
        self.assertEqual(app.status, JobApplication.StatusChoices.inactive)

    def test_deleting_custom_layout_falls_back_to_default(self):
        custom = ApplicationLayout.objects.create(user=self.user, name="fancy")
        app = JobApplication.objects.create(
            user=self.user, posting=self.posting, layout=custom
        )
        custom.delete()
        app.refresh_from_db()
        self.assertEqual(app.layout_id, default_application_layout())


class JobApplicationCrudTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="app_crud", password="pass")
        cls.posting = JobPosting.objects.create(
            user=cls.user, title="Backend dev", posting_text="We need a backend dev."
        )

    def test_create_with_posting_text_creates_posting_inline(self):
        self.client.force_login(self.user)
        r = self.client.post(
            "/api/jac/applications/",
            {"posting_text": "We need a backend dev."},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "draft")
        app = JobApplication.objects.get(pk=r.data["id"])
        self.assertEqual(app.user, self.user)
        self.assertEqual(app.posting.user, self.user)
        self.assertEqual(app.posting.posting_text, "We need a backend dev.")
        self.assertEqual(
            r.data["posting_detail"]["posting_text"], "We need a backend dev."
        )

    def test_create_with_existing_posting(self):
        self.client.force_login(self.user)
        r = self.client.post(
            "/api/jac/applications/", {"posting": self.posting.pk}, format="json"
        )
        self.assertEqual(r.status_code, 201, r.data)
        app = JobApplication.objects.get(pk=r.data["id"])
        self.assertEqual(app.posting_id, self.posting.pk)

    def test_create_requires_exactly_one_posting_source(self):
        self.client.force_login(self.user)
        r = self.client.post("/api/jac/applications/", {}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post(
            "/api/jac/applications/",
            {"posting": self.posting.pk, "posting_text": "also text"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_retrieve_nests_run_summaries(self):
        self.client.force_login(self.user)
        app = JobApplication.objects.create(
            user=self.user, posting=self.posting, cover_letter="hi"
        )
        GenerationRun.objects.create(job_application=app)
        r = self.client.get(f"/api/jac/applications/{app.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["cover_letter"], "hi")
        self.assertEqual(r.data["posting"], self.posting.pk)
        self.assertEqual(len(r.data["runs"]), 1)
        self.assertNotIn("result", r.data["runs"][0])  # summaries stay compact

    def test_patch_updates_editable_fields(self):
        self.client.force_login(self.user)
        app = JobApplication.objects.create(user=self.user, posting=self.posting)
        r = self.client.patch(
            f"/api/jac/applications/{app.pk}/",
            {
                "cover_letter": "rewritten",
                "cv_content": {"skills": [{"id": "skill:1", "label": "Python"}]},
                "status": "sent",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        app.refresh_from_db()
        self.assertEqual(app.cover_letter, "rewritten")
        self.assertEqual(app.cv_content["skills"][0]["label"], "Python")
        self.assertEqual(app.status, "sent")

    def test_posting_is_immutable_after_create(self):
        self.client.force_login(self.user)
        app = JobApplication.objects.create(user=self.user, posting=self.posting)
        other = JobPosting.objects.create(user=self.user, posting_text="other")
        r = self.client.patch(
            f"/api/jac/applications/{app.pk}/", {"posting": other.pk}, format="json"
        )
        self.assertEqual(r.status_code, 400)
        r = self.client.patch(
            f"/api/jac/applications/{app.pk}/",
            {"posting_text": "sneaky"},
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_delete_cascades_runs(self):
        self.client.force_login(self.user)
        app = JobApplication.objects.create(user=self.user, posting=self.posting)
        run = GenerationRun.objects.create(job_application=app)
        r = self.client.delete(f"/api/jac/applications/{app.pk}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(JobApplication.objects.filter(pk=app.pk).exists())
        self.assertFalse(GenerationRun.objects.filter(pk=run.pk).exists())


class JobApplicationContentV2Tests(APITestCase):
    """The structured-content contract (application-content-v2 guide): `letter_meta` is an
    SPA-owned JSON artefact like cv_content, and the posting nests its extracted address."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="app_v2", password="pass")
        cls.posting = JobPosting.objects.create(
            user=cls.user, title="Backend dev", posting_text="We need a backend dev."
        )
        cls.app = JobApplication.objects.create(user=cls.user, posting=cls.posting)

    def test_letter_meta_defaults_empty_and_roundtrips(self):
        self.client.force_login(self.user)
        r = self.client.get(f"/api/jac/applications/{self.app.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["letter_meta"], {})

        meta = {
            "language": "de",
            "subject": "Bewerbung als Backend dev",
            "salutation": "Sehr geehrte Damen und Herren,",
            "date": "2026-07-09",
            "closing": "Mit freundlichen Grüßen,",
            "sender": {"name": "Ada"},
            "recipient": {"company": "ACME"},
        }
        r = self.client.patch(
            f"/api/jac/applications/{self.app.pk}/",
            {"letter_meta": meta},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.app.refresh_from_db()
        self.assertEqual(self.app.letter_meta, meta)

    def test_posting_detail_address_null_until_extracted(self):
        self.client.force_login(self.user)
        r = self.client.get(f"/api/jac/applications/{self.app.pk}/")
        self.assertIsNone(r.data["posting_detail"]["address"])

        JobPostAddress.objects.create(
            job_posting=self.posting, company="ACME", city="Duckburg"
        )
        r = self.client.get(f"/api/jac/applications/{self.app.pk}/")
        addr = r.data["posting_detail"]["address"]
        self.assertEqual(addr["company"], "ACME")
        self.assertEqual(addr["city"], "Duckburg")


class JobApplicationScopingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="app_alice", password="pass")
        cls.bob = User.objects.create_user(username="app_bob", password="pass")
        cls.bob_posting = JobPosting.objects.create(
            user=cls.bob, posting_text="Bob's posting."
        )
        cls.alice_app = _application(cls.alice, posting_text="Alice's posting.")

    def test_list_is_user_scoped(self):
        self.client.force_login(self.bob)
        r = self.client.get("/api/jac/applications/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 0)

    def test_other_user_cannot_retrieve(self):
        self.client.force_login(self.bob)
        r = self.client.get(f"/api/jac/applications/{self.alice_app.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_cannot_bind_another_users_posting(self):
        self.client.force_login(self.alice)
        r = self.client.post(
            "/api/jac/applications/", {"posting": self.bob_posting.pk}, format="json"
        )
        self.assertEqual(r.status_code, 400)


class ApplicationLayoutApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="layout_alice", password="pass")
        cls.bob = User.objects.create_user(username="layout_bob", password="pass")
        cls.system_layout = ApplicationLayout.objects.get(
            pk=default_application_layout()
        )
        cls.alice_layout = ApplicationLayout.objects.create(
            user=cls.alice, name="alice-special"
        )

    def test_list_includes_own_and_system_defaults(self):
        self.client.force_login(self.alice)
        r = self.client.get("/api/jac/layouts/")
        self.assertEqual(r.status_code, 200)
        rows = {row["name"]: row for row in r.data["results"]}
        self.assertIn("default", rows)
        self.assertTrue(rows["default"]["is_default"])
        self.assertIn("alice-special", rows)
        self.assertFalse(rows["alice-special"]["is_default"])

    def test_other_users_layouts_are_invisible(self):
        self.client.force_login(self.bob)
        r = self.client.get("/api/jac/layouts/")
        names = [row["name"] for row in r.data["results"]]
        self.assertNotIn("alice-special", names)

    def test_system_layout_is_read_only(self):
        self.client.force_login(self.alice)
        r = self.client.patch(
            f"/api/jac/layouts/{self.system_layout.pk}/",
            {"name": "hijacked"},
            format="json",
        )
        self.assertEqual(r.status_code, 404)
        r = self.client.delete(f"/api/jac/layouts/{self.system_layout.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_create_own_layout(self):
        self.client.force_login(self.bob)
        r = self.client.post("/api/jac/layouts/", {"name": "minimal"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        layout = ApplicationLayout.objects.get(pk=r.data["id"])
        self.assertEqual(layout.user, self.bob)

    def test_duplicate_name_per_user_is_rejected(self):
        self.client.force_login(self.alice)
        r = self.client.post(
            "/api/jac/layouts/", {"name": "alice-special"}, format="json"
        )
        self.assertEqual(r.status_code, 400)


class RewriteEndpointTests(APITestCase):
    """POST /api/jac/applications/<pk>/rewrite/ — the letter editor's AI passage rewrite.
    Only the passage travels both ways; nothing is persisted. The LLM is patched at
    jac.llm_prompts.complete, so no network. NOTE: the cross-user test passes even before
    the endpoint exists (404 either way) — it pins the contract for the green state."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="rw_user", password="pass")
        cls.other = User.objects.create_user(username="rw_other", password="pass")
        cls.app = _application(cls.user, posting_text="We need a dev.")

    def _post(self, body):
        return self.client.post(
            f"/api/jac/applications/{self.app.pk}/rewrite/", body, format="json"
        )

    def test_rewrites_a_passage(self):
        self.client.force_login(self.user)
        with patch("jac.llm_prompts.complete", return_value="  Polished passage.\n"):
            r = self._post({"text": "I did stuff.", "instruction": "more formal"})
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        self.assertEqual(r.data, {"text": "Polished passage."})

    def test_blank_text_is_400(self):
        self.client.force_login(self.user)
        r = self._post({"text": "   "})
        self.assertEqual(r.status_code, 400)

    def test_overlong_passage_is_400(self):
        self.client.force_login(self.user)
        with patch("jac.llm_prompts.complete", return_value="x") as mock_complete:
            r = self._post({"text": "x" * 5000})
        self.assertEqual(r.status_code, 400)
        mock_complete.assert_not_called()

    def test_empty_model_reply_is_502(self):
        self.client.force_login(self.user)
        with patch("jac.llm_prompts.complete", return_value=""):
            r = self._post({"text": "I did stuff."})
        self.assertEqual(r.status_code, 502)

    def test_foreign_application_is_404(self):
        self.client.force_login(self.other)
        with patch("jac.llm_prompts.complete", return_value="x"):
            r = self._post({"text": "I did stuff."})
        self.assertEqual(r.status_code, 404)
