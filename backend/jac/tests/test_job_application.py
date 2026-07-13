"""JobApplication + ApplicationLayout — model defaults + REST CRUD/scoping.

Structure: `JobPosting 1──< JobApplication 1──< GenerationRun` (`runs`). The application is
the user-editable artefact (tailored CV JSON + cover letter) at `/api/jac/applications/`;
layouts at `/api/jac/layouts/` follow the Domain pattern (system defaults readable by all,
writes own-only). The layout FK is non-null with a callable default resolving to the system
"default" layout — also the SET_DEFAULT target when a custom layout is deleted.
"""

from datetime import date
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
    TransitionError,
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
                "deadline": "2026-09-01",
                "notes": "chase the recruiter",
                # status is read-only over PATCH now — the transition action owns it.
                "status": "sent",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        app.refresh_from_db()
        self.assertEqual(app.cover_letter, "rewritten")
        self.assertEqual(app.cv_content["skills"][0]["label"], "Python")
        self.assertEqual(str(app.deadline), "2026-09-01")
        self.assertEqual(app.notes, "chase the recruiter")
        # The status PATCH was silently ignored (read-only field).
        self.assertEqual(app.status, JobApplication.StatusChoices.draft)

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


class JobApplicationTransitionModelTests(TestCase):
    """`JobApplication.apply_transition` — the guarded state-machine (foundation lifecycle
    guide). It validates the move against `TRANSITIONS`, stamps the side-effect fields, and
    never saves (the caller persists), so a rejected move leaves the row untouched."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="trans_model", password="pass")
        cls.posting = JobPosting.objects.create(user=cls.user, posting_text="x")

    def _app(self, status=JobApplication.StatusChoices.draft):
        return JobApplication.objects.create(
            user=self.user, posting=self.posting, status=status
        )

    def test_approve_stamps_approved_at(self):
        app = self._app()
        app.apply_transition(JobApplication.StatusChoices.approved)
        self.assertEqual(app.status, JobApplication.StatusChoices.approved)
        self.assertIsNotNone(app.approved_at)

    def test_sent_stamps_sent_at_and_delivery_method(self):
        app = self._app(JobApplication.StatusChoices.approved)
        app.apply_transition(
            JobApplication.StatusChoices.sent, delivery_method="manual"
        )
        self.assertEqual(app.status, JobApplication.StatusChoices.sent)
        self.assertIsNotNone(app.sent_at)
        self.assertEqual(app.delivery_method, "manual")
        # Marking sent by hand never sets the backend-sent flag (chunk 2's job).
        self.assertFalse(app.sent_by_system)

    def test_response_stamps_outcome_note_and_responded_at(self):
        app = self._app(JobApplication.StatusChoices.sent)
        app.apply_transition(
            JobApplication.StatusChoices.response,
            response_outcome="interview",
            note="call Tuesday",
        )
        self.assertEqual(app.status, JobApplication.StatusChoices.response)
        self.assertEqual(app.response_outcome, "interview")
        self.assertEqual(app.notes, "call Tuesday")
        self.assertIsNotNone(app.responded_at)

    def test_can_transition_map(self):
        app = self._app()
        self.assertTrue(app.can_transition(JobApplication.StatusChoices.approved))
        self.assertFalse(app.can_transition(JobApplication.StatusChoices.sent))

    def test_illegal_move_raises(self):
        app = self._app()  # draft
        with self.assertRaises(TransitionError):
            app.apply_transition(JobApplication.StatusChoices.sent)

    def test_unknown_target_raises(self):
        app = self._app()
        with self.assertRaises(TransitionError):
            app.apply_transition("bogus")

    def test_unknown_delivery_method_raises(self):
        app = self._app(JobApplication.StatusChoices.approved)
        with self.assertRaises(TransitionError):
            app.apply_transition(
                JobApplication.StatusChoices.sent, delivery_method="carrier_pigeon"
            )

    def test_unknown_outcome_raises(self):
        app = self._app(JobApplication.StatusChoices.sent)
        with self.assertRaises(TransitionError):
            app.apply_transition(
                JobApplication.StatusChoices.response, response_outcome="maybe"
            )


class JobApplicationTransitionApiTests(APITestCase):
    """POST /api/jac/applications/<pk>/transition/ — the only way to change `status` now that
    the serializer field is read-only. Legal moves 200 + stamp; illegal/unknown 400; the
    payload (delivery_method / response_outcome / note) is carried into the model."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="trans_api", password="pass")
        cls.other = User.objects.create_user(username="trans_other", password="pass")
        cls.posting = JobPosting.objects.create(user=cls.user, posting_text="x")

    def _app(self, status=JobApplication.StatusChoices.draft):
        return JobApplication.objects.create(
            user=self.user, posting=self.posting, status=status
        )

    def _post(self, app, body):
        return self.client.post(
            f"/api/jac/applications/{app.pk}/transition/", body, format="json"
        )

    def test_legal_move_returns_200_and_stamps(self):
        self.client.force_login(self.user)
        app = self._app()
        r = self._post(app, {"to": "approved"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "approved")
        self.assertIsNotNone(r.data["approved_at"])

    def test_illegal_move_returns_400_and_does_not_change(self):
        self.client.force_login(self.user)
        app = self._app()  # draft
        r = self._post(app, {"to": "sent"})
        self.assertEqual(r.status_code, 400)
        app.refresh_from_db()
        self.assertEqual(app.status, JobApplication.StatusChoices.draft)

    def test_unknown_target_returns_400(self):
        self.client.force_login(self.user)
        app = self._app()
        r = self._post(app, {"to": "bogus"})
        self.assertEqual(r.status_code, 400)

    def test_missing_target_returns_400(self):
        self.client.force_login(self.user)
        app = self._app()
        r = self._post(app, {})
        self.assertEqual(r.status_code, 400)

    def test_sent_carries_delivery_method(self):
        self.client.force_login(self.user)
        app = self._app(JobApplication.StatusChoices.approved)
        r = self._post(app, {"to": "sent", "delivery_method": "email"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["delivery_method"], "email")
        self.assertIsNotNone(r.data["sent_at"])
        self.assertFalse(r.data["sent_by_system"])

    def test_response_carries_outcome_and_note(self):
        self.client.force_login(self.user)
        app = self._app(JobApplication.StatusChoices.sent)
        r = self._post(
            app,
            {"to": "response", "response_outcome": "offer", "note": "verbal offer"},
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["response_outcome"], "offer")
        self.assertEqual(r.data["notes"], "verbal offer")
        self.assertIsNotNone(r.data["responded_at"])

    def test_foreign_application_is_404(self):
        self.client.force_login(self.other)
        app = self._app()
        r = self._post(app, {"to": "approved"})
        self.assertEqual(r.status_code, 404)


class JobApplicationDeadlineApiTests(APITestCase):
    """`deadline` is user-writable over PATCH (the manual-override half of the deadline
    strategy — the generation task fills it automatically when empty)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="deadline_api", password="pass")
        cls.posting = JobPosting.objects.create(user=cls.user, posting_text="x")

    def test_deadline_defaults_null_and_is_patchable(self):
        self.client.force_login(self.user)
        app = JobApplication.objects.create(user=self.user, posting=self.posting)
        r = self.client.get(f"/api/jac/applications/{app.pk}/")
        self.assertIsNone(r.data["deadline"])

        r = self.client.patch(
            f"/api/jac/applications/{app.pk}/",
            {"deadline": "2026-09-01"},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        app.refresh_from_db()
        self.assertEqual(app.deadline, date(2026, 9, 1))


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


class ChatEndpointTests(APITestCase):
    """POST /api/jac/applications/<pk>/chat/ — the ephemeral letter-refinement chat.
    Client sends its draft body + transcript; nothing is persisted. Standard+ aliases
    only (the UI filters; get_alias_strength is the backstop). The LLM is patched at
    jac.llm_prompts.complete; the strength probe at jac.views.get_alias_strength."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="chat_user", password="pass")
        cls.other = User.objects.create_user(username="chat_other", password="pass")
        cls.app = _application(cls.user, posting_text="We need a dev.")

    def _post(self, body=None):
        payload = {
            "alias": "writer",
            "body": "I build things.",
            "messages": [{"role": "user", "content": "Make it warmer."}],
        }
        payload.update(body or {})
        return self.client.post(
            f"/api/jac/applications/{self.app.pk}/chat/", payload, format="json"
        )

    def _capable(self):
        return patch("jac.views.get_alias_strength", return_value="standard")

    def test_reply_only(self):
        self.client.force_login(self.user)
        with self._capable(), patch(
            "jac.llm_prompts.complete", return_value="Open with the impact."
        ):
            r = self._post()
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        self.assertEqual(r.data, {"reply": "Open with the impact.", "revision": None})

    def test_revision_extracted(self):
        self.client.force_login(self.user)
        raw = "Tighter version below.\nREVISED BODY:\nI ship things."
        with self._capable(), patch("jac.llm_prompts.complete", return_value=raw):
            r = self._post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["revision"], "I ship things.")

    def test_empty_messages_is_400(self):
        self.client.force_login(self.user)
        with self._capable():
            r = self._post({"messages": []})
        self.assertEqual(r.status_code, 400)

    def test_last_message_must_be_user(self):
        self.client.force_login(self.user)
        with self._capable():
            r = self._post(
                {
                    "messages": [
                        {"role": "user", "content": "Hi"},
                        {"role": "assistant", "content": "Hello"},
                    ]
                }
            )
        self.assertEqual(r.status_code, 400)

    def test_light_alias_is_400(self):
        self.client.force_login(self.user)
        with patch("jac.views.get_alias_strength", return_value="light"), patch(
            "jac.llm_prompts.complete"
        ) as mock_complete:
            r = self._post()
        self.assertEqual(r.status_code, 400)
        mock_complete.assert_not_called()

    def test_overlong_transcript_is_400_without_llm_call(self):
        self.client.force_login(self.user)
        with self._capable(), patch("jac.llm_prompts.complete") as mock_complete:
            r = self._post(
                {"messages": [{"role": "user", "content": "x" * 7000}]}
            )
        self.assertEqual(r.status_code, 400)
        mock_complete.assert_not_called()

    def test_empty_model_reply_is_502(self):
        self.client.force_login(self.user)
        with self._capable(), patch("jac.llm_prompts.complete", return_value=""):
            r = self._post()
        self.assertEqual(r.status_code, 502)

    def test_foreign_application_is_404(self):
        self.client.force_login(self.other)
        with self._capable(), patch("jac.llm_prompts.complete", return_value="x"):
            r = self._post()
        self.assertEqual(r.status_code, 404)


class FindAddressEndpointTests(APITestCase):
    """POST /api/jac/applications/<pk>/find_address/ — web-search the employer's postal
    address for the recipient block. Capability-gated (400 for a non-web-search alias),
    502 when nothing usable is found, nothing persisted. The LLM boundary is patched at
    jac.llm_prompts.web_search (+ can_web_search in both consuming namespaces)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="fa_user", password="pass")
        cls.other = User.objects.create_user(username="fa_other", password="pass")
        cls.app = _application(cls.user, posting_text="Acme GmbH is hiring a dev.")

    def _post(self, body=None):
        return self.client.post(
            f"/api/jac/applications/{self.app.pk}/find_address/",
            body or {},
            format="json",
        )

    def _capable(self):
        return (
            patch("jac.views.can_web_search", return_value=True),
            patch("jac.llm_prompts.can_web_search", return_value=True),
        )

    def test_finds_and_returns_the_address(self):
        self.client.force_login(self.user)
        res = {
            "text": "company: Acme GmbH\nstreet: Musterweg 5\nzip: 10115\ncity: Berlin",
            "sources": ["https://acme.example/imprint"],
        }
        gate1, gate2 = self._capable()
        with gate1, gate2, patch("jac.llm_prompts.web_search", return_value=res):
            r = self._post({"alias": "opus"})
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        self.assertEqual(r.data["address"]["city"], "Berlin")
        self.assertEqual(r.data["sources"], ["https://acme.example/imprint"])

    def test_incapable_alias_is_400(self):
        self.client.force_login(self.user)
        with (
            patch("jac.views.can_web_search", return_value=False),
            patch("jac.llm_prompts.web_search") as ws,
        ):
            r = self._post({"alias": "default"})
        self.assertEqual(r.status_code, 400)
        ws.assert_not_called()

    def test_nothing_found_is_502(self):
        self.client.force_login(self.user)
        gate1, gate2 = self._capable()
        with (
            gate1,
            gate2,
            patch(
                "jac.llm_prompts.web_search",
                return_value={"text": "No idea, sorry.", "sources": []},
            ),
        ):
            r = self._post({"alias": "opus"})
        self.assertEqual(r.status_code, 502)

    def test_foreign_application_is_404(self):
        self.client.force_login(self.other)
        gate1, gate2 = self._capable()
        with gate1, gate2, patch("jac.llm_prompts.web_search", return_value={"text": "", "sources": []}):
            r = self._post({"alias": "opus"})
        self.assertEqual(r.status_code, 404)
