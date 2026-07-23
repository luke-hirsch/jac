"""ApplicationAttachment upload API: PDF/size validation, owner-scoping, ordering, delete.

Target = [frontend]-cert-attachments (the backend half). Starts RED: `ApplicationAttachment` and
the `attachments` route don't exist yet. File uploads land under a throwaway MEDIA_ROOT.
"""

import shutil
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from jac.models import ApplicationAttachment

from ._helpers import make_application, make_user

_TMP_MEDIA = tempfile.mkdtemp(prefix="jacattach-tests-")


def tearDownModule():
    shutil.rmtree(_TMP_MEDIA, ignore_errors=True)


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class AttachmentApiTests(APITestCase):
    URL = "/api/jac/attachments/"

    def setUp(self):
        self.user = make_user()
        self.other = make_user("bob")
        self.app = make_application(self.user)
        self.client.force_login(self.user)

    def _pdf(self, name="c.pdf", data=b"%PDF-1.5\nhello"):
        return SimpleUploadedFile(name, data, content_type="application/pdf")

    def test_upload_pdf(self):
        res = self.client.post(
            self.URL,
            {
                "application": self.app.pk,
                "file": self._pdf(),
                "label": "Zeugnisse",
                "position": 0,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(ApplicationAttachment.objects.count(), 1)

    def test_reject_non_pdf(self):
        res = self.client.post(
            self.URL,
            {
                "application": self.app.pk,
                "file": SimpleUploadedFile(
                    "x.pdf", b"not a pdf", content_type="application/pdf"
                ),
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 400)

    def test_reject_oversize(self):
        with patch("jac.serializers.ApplicationAttachmentSerializer._MAX_BYTES", 8):
            res = self.client.post(
                self.URL,
                {"application": self.app.pk, "file": self._pdf(data=b"%PDF- and more")},
                format="multipart",
            )
        self.assertEqual(res.status_code, 400)

    def test_list_scoped_to_owner(self):
        ApplicationAttachment.objects.create(
            application=self.app, file=self._pdf(), position=0
        )
        self.client.force_login(self.other)
        res = self.client.get(f"{self.URL}?application={self.app.pk}")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        results = body.get("results", body) if isinstance(body, dict) else body
        self.assertEqual(len(results), 0)

    def test_ordering_by_position(self):
        ApplicationAttachment.objects.create(
            application=self.app, file=self._pdf(), label="B", position=1
        )
        ApplicationAttachment.objects.create(
            application=self.app, file=self._pdf(), label="A", position=0
        )
        res = self.client.get(f"{self.URL}?application={self.app.pk}")
        body = res.json()
        results = body.get("results", body) if isinstance(body, dict) else body
        self.assertEqual([a["label"] for a in results], ["A", "B"])

    def test_reject_foreign_application(self):
        others_app = make_application(self.other)
        res = self.client.post(
            self.URL,
            {"application": others_app.pk, "file": self._pdf()},
            format="multipart",
        )
        self.assertIn(res.status_code, (400, 403))

    def test_delete(self):
        att = ApplicationAttachment.objects.create(
            application=self.app, file=self._pdf(), position=0
        )
        res = self.client.delete(f"{self.URL}{att.pk}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(ApplicationAttachment.objects.filter(pk=att.pk).exists())
