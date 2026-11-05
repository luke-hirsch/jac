"""What the legal pages promise, and the parts of it code can prove (guide 07).

- /legal/<page>/ renders for anyone, on every host. The operator — not a handle's owner —
  answers for every subdomain, so the imprint reads the operator's identity from their
  profile (PORTFOLIO_OWNER_USERNAME). Nothing personal is hardcoded in the public repo.
- The Django-rendered pages link each other and are linked from the landing. The SPA footer is a
  component (no jsdom here), so it's checked in the guide's Verification.
- Art. 17: deleting an account removes the user's files (public + private, orphans included)
  and their LLM request logs, not only the rows that cascade from auth.User.

The deletion class carries the `files` skip group: real files under throwaway roots.
"""

import base64
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings, tag
from rest_framework.test import APITestCase

from jac.models import CvAttachment
from llm_connector.models import LLMRequestLog
from lukehirsch.test_media import own_media_roots
from spa.models import PersonalityProfile, PortfolioBlock, UserProfile

PDF = b"%PDF-1.5\nzeugnis"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
PAGES = ("imprint", "privacy", "terms")


@override_settings(
    PORTFOLIO_OWNER_USERNAME="lukas",
    BASE_DOMAIN="testserver",
    ALLOWED_HOSTS=[".testserver"],
    LEGAL_CONTACT_EMAIL="",
)
class LegalPagesTests(TestCase):
    def setUp(self):
        self.lukas = User.objects.create_user(
            username="lukas",
            password="pw",
            email="lukas@example.com",
            first_name="Lukas",
            last_name="Hirsch",
        )
        UserProfile.objects.filter(user=self.lukas).update(
            street="Musterstraße 1", zip="10115", city="Berlin", country="Deutschland"
        )

    def test_every_legal_page_renders_anonymously(self):
        for page in PAGES:
            r = self.client.get(f"/legal/{page}/")
            self.assertEqual(r.status_code, 200, page)
            self.assertIn("text/html", r["Content-Type"])

    def test_an_unknown_page_is_404(self):
        self.assertEqual(self.client.get("/legal/cookies/").status_code, 404)

    def test_the_imprint_names_the_operator_from_their_profile(self):
        r = self.client.get("/legal/imprint/")
        for fact in (
            "Lukas Hirsch",
            "Musterstraße 1",
            "10115",
            "Berlin",
            "lukas@example.com",
        ):
            self.assertContains(r, fact)

    def test_the_imprint_prints_the_real_name_over_the_display_name(self):
        """§ 5 DDG wants the operator's name; a display name can be anything."""
        UserProfile.objects.filter(user=self.lukas).update(display_name="lukehirsch")
        r = self.client.get("/legal/imprint/")
        self.assertContains(r, "Lukas Hirsch")
        self.assertNotContains(r, "lukehirsch")

    @override_settings(LEGAL_CONTACT_EMAIL="impressum@example.com")
    def test_a_dedicated_contact_address_wins_over_the_login_email(self):
        r = self.client.get("/legal/imprint/")
        self.assertContains(r, "impressum@example.com")
        self.assertNotContains(r, "lukas@example.com")

    @override_settings(LLM_LOG_RETENTION_DAYS=30)
    def test_the_privacy_page_states_the_configured_log_retention(self):
        """The housekeeping job and the promise read one setting, so they can't drift apart."""
        self.assertContains(self.client.get("/legal/privacy/"), "nach 30 Tagen")

    def test_a_handle_host_shows_the_operators_imprint(self):
        """jane.<base> is Jane's portfolio, but Lukas runs the service: his imprint, not hers."""
        User.objects.create_user(username="jane", password="pw", first_name="Janet")
        r = self.client.get("/legal/imprint/", HTTP_HOST="jane.testserver")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Lukas Hirsch")
        self.assertNotContains(r, "Janet")

    def test_every_legal_page_links_all_three(self):
        for page in PAGES:
            r = self.client.get(f"/legal/{page}/")
            for target in PAGES:
                self.assertContains(r, f'href="/legal/{target}/"', msg_prefix=page)

    def test_the_legal_pages_name_no_infrastructure(self):
        """They say what data goes where and why. What it runs on is nobody's business."""
        for page in PAGES:
            r = self.client.get(f"/legal/{page}/")
            for word in (
                "Mac mini",
                "Grafikkarte",
                "zu Hause",
                "Hardware",
                "Docker",
                "nginx",
                "Reverse Proxy",
                "IONOS",
                "Strato",
            ):
                self.assertNotContains(r, word, msg_prefix=page)

    def test_the_landing_links_the_legal_pages(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        for target in PAGES:
            self.assertContains(r, f'href="/legal/{target}/"')


def _png(name="x.png"):
    return SimpleUploadedFile(name, PNG, content_type="image/png")


@tag("files")
@override_settings(**own_media_roots())
class AccountDeletionTests(APITestCase):
    """DELETE /api/spa/account/ keeps the privacy policy's Art. 17 promise."""

    URL = "/api/spa/account/"

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")

        self.attachment = CvAttachment.objects.create(
            user=self.alice, file=SimpleUploadedFile("zeugnis.pdf", PDF)
        )
        profile = UserProfile.objects.get(user=self.alice)
        profile.avatar = _png("me.png")
        profile.signature = _png("sig.png")
        profile.save()
        self.old_signature = profile.signature.path
        # Re-upload: the first signature file is now referenced by no row (an orphan).
        profile.signature = _png("sig.png")
        profile.save()
        self.profile = profile
        self.block = PortfolioBlock.objects.create(
            user=self.alice, kind="image", image=_png("block.png")
        )

        self.bobs_attachment = CvAttachment.objects.create(
            user=self.bob, file=SimpleUploadedFile("bob.pdf", PDF)
        )
        for user in (self.alice, self.bob):
            LLMRequestLog.objects.create(
                user=user, provider="fake", model="m", request_messages=[]
            )

        self.client.force_login(self.alice)

    def _delete_account(self):
        # The view demands a fresh re-auth (allauth); that flow has its own tests.
        with (
            patch("spa.views.did_recently_authenticate", return_value=True),
            self.captureOnCommitCallbacks(execute=True),
        ):
            r = self.client.delete(self.URL)
        self.assertEqual(r.status_code, 204, r.content)

    @override_settings(
        PORTFOLIO_OWNER_USERNAME="bob", LEGAL_CONTACT_EMAIL="ops@example.com"
    )
    def test_the_operator_learns_the_deleted_id_and_nothing_else(self):
        """Privacy section 10: a restore brings deleted accounts back, and they get deleted again.
        So the list has to live outside the backups, and it holds a number."""
        User.objects.filter(pk=self.alice.pk).update(email="alice@example.com")
        alice_id = self.alice.pk
        self._delete_account()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["ops@example.com"])
        text = f"{message.subject} {message.body}"
        self.assertIn(str(alice_id), text)
        self.assertNotIn("alice", text)

    def test_no_operator_means_no_mail_and_the_deletion_still_works(self):
        self._delete_account()
        self.assertEqual(mail.outbox, [])
        self.assertFalse(User.objects.filter(username="alice").exists())

    def test_the_users_files_are_gone_from_disk(self):
        paths = [
            self.attachment.file.path,
            self.profile.avatar.path,
            self.profile.signature.path,
            self.block.image.path,
        ]
        for path in paths:
            self.assertTrue(Path(path).exists(), path)
        self._delete_account()
        for path in paths:
            self.assertFalse(Path(path).exists(), f"left on disk: {path}")

    def test_orphaned_files_from_earlier_uploads_are_gone_too(self):
        self.assertTrue(Path(self.old_signature).exists())
        self._delete_account()
        self.assertFalse(Path(self.old_signature).exists(), self.old_signature)

    def test_another_users_files_survive(self):
        self._delete_account()
        self.assertTrue(Path(self.bobs_attachment.file.path).exists())

    def test_the_users_llm_logs_are_deleted_not_orphaned(self):
        """SET_NULL kept every prompt row alive, minus the name on it."""
        pk = self.alice.pk
        self._delete_account()
        self.assertFalse(LLMRequestLog.objects.filter(user_id=pk).exists())
        self.assertFalse(LLMRequestLog.objects.filter(user__isnull=True).exists())
        self.assertTrue(LLMRequestLog.objects.filter(user=self.bob).exists())

    def test_the_rows_cascade(self):
        pk = self.alice.pk
        self._delete_account()
        self.assertFalse(User.objects.filter(pk=pk).exists())
        self.assertFalse(PersonalityProfile.objects.filter(user_id=pk).exists())
        self.assertFalse(CvAttachment.objects.filter(user_id=pk).exists())
        self.assertFalse(PortfolioBlock.objects.filter(user_id=pk).exists())


@tag("files")
@override_settings(**own_media_roots())
class SingleDeleteRemovesTheFileTests(APITestCase):
    """The same promise at row level: deleting one upload deletes its file."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.client.force_login(self.alice)

    def test_deleting_an_attachment_deletes_its_pdf(self):
        att = CvAttachment.objects.create(
            user=self.alice, file=SimpleUploadedFile("z.pdf", PDF)
        )
        path = att.file.path
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.delete(f"/api/jac/attachments/{att.pk}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Path(path).exists(), path)

    def test_deleting_an_image_block_deletes_its_image(self):
        block = PortfolioBlock.objects.create(
            user=self.alice, kind="image", image=_png("b.png")
        )
        path = block.image.path
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.delete(f"/api/spa/portfolio/manage/blocks/{block.pk}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Path(path).exists(), path)
