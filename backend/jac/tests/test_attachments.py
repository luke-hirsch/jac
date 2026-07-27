"""CvAttachment library API: PDF/size validation, optional entry link (owned / foreign /
at-most-one), owner-scoping, entry-link filtering, delete — plus the application's
`attachments` id-list (ownership-validated pick from the library).

The attachment is a reusable, user-owned career-DB item (not per-application); an application
references chosen attachments by id in `JobApplication.attachments`. File uploads land under a
throwaway MEDIA_ROOT.
"""

import shutil
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from jac.models import Certification, CvAttachment, Education

from ._helpers import make_application, make_user

_TMP_MEDIA = tempfile.mkdtemp(prefix="jacattach-tests-")


def tearDownModule():
    shutil.rmtree(_TMP_MEDIA, ignore_errors=True)


def _education(user, institution="TU Berlin"):
    return Education.objects.create(
        user=user, institution=institution, started="2015-10-01"
    )


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class CvAttachmentApiTests(APITestCase):
    URL = "/api/jac/attachments/"

    def setUp(self):
        self.user = make_user()
        self.other = make_user("bob")
        self.client.force_login(self.user)

    def _pdf(self, name="c.pdf", data=b"%PDF-1.5\nhello"):
        return SimpleUploadedFile(name, data, content_type="application/pdf")

    def test_upload_pdf(self):
        res = self.client.post(
            self.URL,
            {"file": self._pdf(), "label": "Zeugnisse"},
            format="multipart",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(CvAttachment.objects.filter(user=self.user).count(), 1)
        # `user` is bound from the request, never echoed back as a client field.
        self.assertNotIn("user", res.json())

    def test_reject_non_pdf(self):
        res = self.client.post(
            self.URL,
            {"file": SimpleUploadedFile("x.pdf", b"not a pdf")},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400)

    def test_reject_oversize(self):
        with patch("jac.serializers.CvAttachmentSerializer._MAX_BYTES", 8):
            res = self.client.post(
                self.URL,
                {"file": self._pdf(data=b"%PDF- and more")},
                format="multipart",
            )
        self.assertEqual(res.status_code, 400)

    def test_link_to_owned_entry(self):
        edu = _education(self.user)
        res = self.client.post(
            self.URL,
            {"file": self._pdf(), "label": "Diploma", "education": edu.pk},
            format="multipart",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["education"], edu.pk)

    def test_reject_foreign_entry_link(self):
        foreign_edu = _education(self.other)
        res = self.client.post(
            self.URL,
            {"file": self._pdf(), "education": foreign_edu.pk},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400)

    def test_reject_two_links(self):
        edu = _education(self.user)
        cert = Certification.objects.create(
            user=self.user, name="AWS SAA", issuer="Amazon"
        )
        res = self.client.post(
            self.URL,
            {"file": self._pdf(), "education": edu.pk, "certification": cert.pk},
            format="multipart",
        )
        self.assertEqual(res.status_code, 400)

    def test_list_scoped_to_owner(self):
        CvAttachment.objects.create(user=self.user, file=self._pdf())
        self.client.force_login(self.other)
        res = self.client.get(self.URL)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        results = body.get("results", body) if isinstance(body, dict) else body
        self.assertEqual(len(results), 0)

    def test_filter_by_entry_link(self):
        edu = _education(self.user)
        CvAttachment.objects.create(
            user=self.user, file=self._pdf(), label="linked", education=edu
        )
        CvAttachment.objects.create(user=self.user, file=self._pdf(), label="loose")
        res = self.client.get(f"{self.URL}?education={edu.pk}")
        body = res.json()
        results = body.get("results", body) if isinstance(body, dict) else body
        self.assertEqual([a["label"] for a in results], ["linked"])

    def test_delete(self):
        att = CvAttachment.objects.create(user=self.user, file=self._pdf())
        res = self.client.delete(f"{self.URL}{att.pk}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(CvAttachment.objects.filter(pk=att.pk).exists())


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class ApplicationAttachmentSelectionTests(APITestCase):
    """The application's `attachments` field = an ordered, ownership-validated id list."""

    def setUp(self):
        self.user = make_user()
        self.other = make_user("bob")
        self.app = make_application(self.user)
        self.client.force_login(self.user)

    def _att(self, user):
        return CvAttachment.objects.create(
            user=user, file=SimpleUploadedFile("c.pdf", b"%PDF-1.5\nx")
        )

    def test_select_own_attachments(self):
        a, b = self._att(self.user), self._att(self.user)
        res = self.client.patch(
            f"/api/jac/applications/{self.app.pk}/",
            {"attachments": [b.pk, a.pk]},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["attachments"], [b.pk, a.pk])  # order preserved

    def test_reject_foreign_attachment(self):
        foreign = self._att(self.other)
        res = self.client.patch(
            f"/api/jac/applications/{self.app.pk}/",
            {"attachments": [foreign.pk]},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
