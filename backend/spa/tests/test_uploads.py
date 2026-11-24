"""User uploads: where they land and how they're handed back (guide 03, gaps 8 + 12).

- Certificates (`CvAttachment.file`) and signatures (`UserProfile.signature`) are private: they
  live in PrivateStorage outside MEDIA_ROOT, under unguessable names, and only come back through
  owner-checked views (dev: Django streams; prod: X-Accel-Redirect to nginx's internal location).
- Layout templates stay public (/media/), so they must be what they claim: a JSON object, stored
  as `<uuid>.json` whatever the uploader named it — nginx picks the Content-Type by extension.
- A block's audio or video is public too, so the same holds: its first bytes decide what it is
  (`spa.blocks.sniff_media`), it's stored as `<uuid>.<that extension>`, and it fits the per-file
  cap and the per-user quota (guide 26).

Housed in spa like test_settings_hardening: the mechanism is project-level (lukehirsch/storage.py,
lukehirsch/private_media.py) and spans jac + spa consumers. Carries the `files` skip group: real
files on disk under throwaway roots.
"""

import base64
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings, tag
from rest_framework.test import APITestCase

from jac.models import ApplicationLayout, CvAttachment
from lukehirsch.test_media import own_media_roots
from spa.models import PortfolioBlock, UserProfile

PDF = b"%PDF-1.5\nhello"
# A valid 1x1 transparent PNG (Pillow must accept it as an image).
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
UUID_HEX = "[0-9a-f]{32}"


def _ogg(packet: bytes) -> bytes:
    """An Ogg page header with one segment, then the stream's first packet."""
    header = b"OggS\x00\x02" + b"\x00" * 8 + b"\x01\x00\x00\x00" + b"\x00" * 8
    return header + b"\x01" + bytes([len(packet)]) + packet + b"\x00" * 32


# The first bytes of each container, which is all `sniff_media` reads; the rest is padding.
PAD = b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + PAD
M4A = b"\x00\x00\x00\x1cftypM4A \x00\x00\x00\x00M4A mp42isom\x00\x00\x00\x00" + PAD
WEBM = (
    b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04"
    b"\x42\xf3\x81\x08\x42\x82\x84webm\x42\x87\x81\x04\x42\x85\x81\x02" + PAD
)
MP3_TAGGED = b"ID3\x04\x00\x00\x00\x00\x00\x00" + PAD
MP3_FRAME = b"\xff\xfb\x90\x64" + PAD
OGG_VORBIS = _ogg(b"\x01vorbis\x00\x00\x00\x00\x02\x44\xac\x00\x00")
OGG_OPUS = _ogg(b"OpusHead\x01\x02\x38\x01\x80\xbb\x00\x00")
# Lookalikes: the same container formats, holding what a browser won't play or isn't sound.
MOV = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  " + PAD
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + PAD
MKV = (
    b"\x1a\x45\xdf\xa3\xa3\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04"
    b"\x42\xf3\x81\x08\x42\x82\x88matroska\x42\x87\x81\x04\x42\x85\x81\x02" + PAD
)
AAC_ADTS = b"\xff\xf1\x50\x80\x02\x1f\xfc" + PAD
OGG_THEORA = _ogg(b"\x80theora\x03\x02\x01\x00\x14\x00\x0f")
HTML = b"<!doctype html><script>alert(document.cookie)</script>" + PAD


def _under(path, root) -> bool:
    # resolve(): macOS tmp dirs sit behind the /var -> /private/var symlink.
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def _body(response) -> bytes:
    # Don't close the response here: the test client does once the content is consumed, with
    # close_old_connections unplugged. A second close() really drops the test's connection.
    return b"".join(response.streaming_content)


def _results(body):
    return body.get("results", body) if isinstance(body, dict) else body


@tag("files")
@override_settings(**own_media_roots(), PRIVATE_MEDIA_ACCEL=False)
class PrivateAttachmentTests(APITestCase):
    URL = "/api/jac/attachments/"

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")
        self.client.force_login(self.alice)

    def _upload(self, name="Zeugnis_Lukas.pdf"):
        res = self.client.post(
            self.URL,
            {
                "file": SimpleUploadedFile(name, PDF, content_type="application/pdf"),
                "label": "Arbeitszeugnis",
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        return CvAttachment.objects.get(pk=body["id"]), body

    def test_upload_lands_in_private_storage_under_an_unguessable_name(self):
        att, _ = self._upload("Zeugnis_Lukas.pdf")
        self.assertRegex(
            att.file.name, rf"^cv_attachments/{self.alice.pk}/{UUID_HEX}\.pdf$"
        )
        self.assertTrue(_under(att.file.path, settings.PRIVATE_MEDIA_ROOT), att.file.path)
        self.assertTrue(Path(att.file.path).exists())
        self.assertFalse(
            list(Path(settings.MEDIA_ROOT).rglob("*.pdf")), "a copy leaked to /media/"
        )

    def test_api_returns_the_owner_checked_path_not_a_media_url(self):
        att, body = self._upload()
        self.assertTrue(
            body["file"].startswith(f"/api/jac/attachments/{att.pk}/file/"),
            body["file"],
        )
        listed = _results(self.client.get(self.URL).json())
        self.assertEqual(listed[0]["file"], body["file"])

    def test_the_owner_gets_the_pdf(self):
        _, body = self._upload()
        res = self.client.get(body["file"])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")
        self.assertTrue(res["Content-Disposition"].startswith("inline"))
        self.assertIn("no-store", res["Cache-Control"])
        self.assertEqual(_body(res), PDF)

    @override_settings(PRIVATE_MEDIA_ACCEL=True)
    def test_prod_hands_the_file_to_nginx(self):
        att, body = self._upload()
        res = self.client.get(body["file"])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["X-Accel-Redirect"], f"/_private/{att.file.name}")
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertEqual(res.content, b"", "nginx streams the bytes, not Django")

    def test_another_user_gets_404(self):
        _, body = self._upload()
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(body["file"]).status_code, 404)

    def test_anonymous_is_refused(self):
        _, body = self._upload()
        self.client.logout()
        self.assertEqual(self.client.get(body["file"]).status_code, 403)

    def test_a_file_missing_on_disk_is_404_not_500(self):
        att, body = self._upload()
        Path(att.file.path).unlink()
        self.assertEqual(self.client.get(body["file"]).status_code, 404)

    def test_deleting_an_attachment_takes_its_file_along(self):
        """The quota counts the rows: upload + delete must not fill the disk under it."""
        att, _ = self._upload()
        path = att.file.path
        self.assertEqual(self.client.delete(f"{self.URL}{att.pk}/").status_code, 204)
        self.assertFalse(Path(path).exists(), path)

    def test_replacing_the_file_takes_the_old_one_along(self):
        att, _ = self._upload()
        old_path = att.file.path
        res = self.client.patch(
            f"{self.URL}{att.pk}/",
            {"file": SimpleUploadedFile("new.pdf", PDF)},
            format="multipart",
        )
        self.assertEqual(res.status_code, 200, res.content)
        att.refresh_from_db()
        self.assertNotEqual(att.file.path, old_path)
        self.assertFalse(Path(old_path).exists(), old_path)
        self.assertTrue(Path(att.file.path).exists())

    def test_a_label_edit_keeps_the_file(self):
        att, _ = self._upload()
        res = self.client.patch(
            f"{self.URL}{att.pk}/", {"label": "Zeugnis"}, format="multipart"
        )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(Path(att.file.path).exists())


@tag("files")
@override_settings(**own_media_roots(), PRIVATE_MEDIA_ACCEL=False)
class PrivateSignatureTests(APITestCase):
    PROFILE = "/api/spa/profile/"
    SIGNATURE = "/api/spa/profile/signature/"

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.client.force_login(self.alice)

    def _upload(self, name="my-signature.png"):
        res = self.client.patch(
            self.PROFILE,
            {"signature": SimpleUploadedFile(name, PNG, content_type="image/png")},
            format="multipart",
        )
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_upload_lands_in_private_storage_under_an_unguessable_name(self):
        self._upload("my-signature.png")
        signature = UserProfile.objects.get(user=self.alice).signature
        self.assertRegex(
            signature.name, rf"^signatures/{self.alice.pk}/{UUID_HEX}\.png$"
        )
        self.assertTrue(_under(signature.path, settings.PRIVATE_MEDIA_ROOT), signature.path)
        # Not any *.png: other tests in this module leave public avatars in the same folder.
        self.assertFalse(
            list(Path(settings.MEDIA_ROOT).glob("signatures/**/*.png")), "a copy leaked to /media/"
        )

    def test_profile_returns_the_owner_only_path(self):
        body = self._upload()
        self.assertTrue(body["signature"].startswith(self.SIGNATURE), body["signature"])
        self.assertEqual(
            self.client.get(self.PROFILE).json()["signature"], body["signature"]
        )

    def test_a_new_upload_changes_the_url(self):
        # Same endpoint for every upload: the ?v= suffix is what makes <img> refetch.
        first = self._upload()["signature"]
        second = self._upload()["signature"]
        self.assertNotEqual(first, second)

    def test_the_owner_gets_the_image(self):
        body = self._upload()
        res = self.client.get(body["signature"])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "image/png")
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")
        self.assertEqual(_body(res), PNG)

    def test_anonymous_is_refused(self):
        self._upload()
        self.client.logout()
        self.assertEqual(self.client.get(self.SIGNATURE).status_code, 403)

    def test_no_signature_is_404(self):
        self.assertEqual(self.client.get(self.SIGNATURE).status_code, 404)


@tag("files")
@override_settings(**own_media_roots())
class LayoutTemplateUploadTests(APITestCase):
    """/media/ is public and served from our own origin: an uploaded `x.html` would run as a
    page on jac. — next to the session. A template is a JSON object or nothing."""

    URL = "/api/jac/layouts/"

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.client.force_login(self.alice)

    def _post(self, data: bytes, name="layout.json", layout_name="mine"):
        return self.client.post(
            self.URL,
            {"name": layout_name, "template": SimpleUploadedFile(name, data)},
            format="multipart",
        )

    def test_json_is_stored_under_a_json_name_whatever_it_was_called(self):
        res = self._post(b'{"page": {"size": "A4"}}', name="evil.html")
        self.assertEqual(res.status_code, 201, res.content)
        layout = ApplicationLayout.objects.get(pk=res.json()["id"])
        self.assertRegex(
            layout.template.name, rf"^application_layouts/{UUID_HEX}\.json$"
        )

    def test_html_is_refused(self):
        res = self._post(b"<script>alert(document.cookie)</script>", name="x.json")
        self.assertEqual(res.status_code, 400)

    def test_a_pdf_polyglot_is_refused(self):
        res = self._post(b"%PDF-<script>alert(1)</script>", name="x.html")
        self.assertEqual(res.status_code, 400)

    def test_a_json_array_is_refused(self):
        self.assertEqual(self._post(b"[1, 2, 3]").status_code, 400)

    def test_oversize_is_refused(self):
        with patch(
            "jac.serializers.ApplicationLayoutSerializer._MAX_TEMPLATE_BYTES", 8
        ):
            res = self._post(b'{"page": {"size": "A4"}}')
        self.assertEqual(res.status_code, 400)


PROFILE = "/api/spa/profile/"
ATTACHMENTS = "/api/jac/attachments/"
BLOCKS = "/api/spa/portfolio/manage/blocks/"
LAYOUTS = "/api/jac/layouts/"


def _png():
    return SimpleUploadedFile("image.png", PNG, content_type="image/png")


@tag("files")
@override_settings(**own_media_roots())
class ImageSizeTests(APITestCase):
    """Attachments and templates had a cap; images only had nginx's 12 MB."""

    def setUp(self):
        self.client.force_login(User.objects.create_user(username="alice", password="pw"))

    def _refused(self, res, field):
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn(field, res.json())

    @patch("spa.serializers.MAX_IMAGE_BYTES", 8)
    def test_an_oversized_avatar_is_refused(self):
        res = self.client.patch(PROFILE, {"avatar": _png()}, format="multipart")
        self._refused(res, "avatar")

    @patch("spa.serializers.MAX_IMAGE_BYTES", 8)
    def test_an_oversized_signature_is_refused(self):
        res = self.client.patch(PROFILE, {"signature": _png()}, format="multipart")
        self._refused(res, "signature")

    @patch("spa.serializers.MAX_IMAGE_BYTES", 8)
    def test_an_oversized_block_image_is_refused(self):
        res = self.client.post(
            BLOCKS, {"kind": "image", "image": _png()}, format="multipart"
        )
        self._refused(res, "image")
        self.assertFalse(PortfolioBlock.objects.exists())

    def test_an_image_under_the_cap_is_fine(self):
        res = self.client.patch(PROFILE, {"avatar": _png()}, format="multipart")
        self.assertEqual(res.status_code, 200, res.content)
        res = self.client.post(
            BLOCKS, {"kind": "image", "image": _png()}, format="multipart"
        )
        self.assertEqual(res.status_code, 201, res.content)


ONE_UPLOAD_A_DAY = {
    **settings.REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
        "uploads": "1/day",
    },
}


@tag("files")
@override_settings(**own_media_roots(), REST_FRAMEWORK=ONE_UPLOAD_A_DAY)
class UploadThrottleTests(APITestCase):
    """One rate over everything that puts a file on disk: a loop on any of them fills it."""

    def setUp(self):
        cache.clear()  # scoped-throttle history lives in the default cache
        self.addCleanup(cache.clear)
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.client.force_login(self.alice)

    def _attachment(self):
        return self.client.post(
            ATTACHMENTS, {"file": SimpleUploadedFile("c.pdf", PDF)}, format="multipart"
        )

    def test_every_endpoint_that_takes_a_file_shares_the_rate(self):
        self.assertEqual(self._attachment().status_code, 201)
        refused = {
            "attachment": self._attachment(),
            "avatar": self.client.patch(PROFILE, {"avatar": _png()}, format="multipart"),
            "signature": self.client.patch(
                PROFILE, {"signature": _png()}, format="multipart"
            ),
            "block": self.client.post(
                BLOCKS, {"kind": "image", "image": _png()}, format="multipart"
            ),
            "block media": self.client.post(
                BLOCKS, {"kind": "media", "media": _media()}, format="multipart"
            ),
            "layout": self.client.post(
                LAYOUTS,
                {"name": "mine", "template": SimpleUploadedFile("l.json", b"{}")},
                format="multipart",
            ),
        }
        for name, res in refused.items():
            self.assertEqual(res.status_code, 429, name)

    def test_replacing_a_blocks_image_counts_too(self):
        self.assertEqual(self._attachment().status_code, 201)
        block = PortfolioBlock.objects.create(user=self.alice, kind="text", body="x")
        res = self.client.patch(
            f"{BLOCKS}{block.pk}/", {"image": _png()}, format="multipart"
        )
        self.assertEqual(res.status_code, 429)

    def test_reads_and_plain_edits_dont_count(self):
        self.assertEqual(self._attachment().status_code, 201)
        self.assertEqual(self.client.get(ATTACHMENTS).status_code, 200)
        self.assertEqual(self.client.get(PROFILE).status_code, 200)
        self.assertEqual(self.client.get(BLOCKS).status_code, 200)
        self.assertEqual(self.client.get(LAYOUTS).status_code, 200)
        res = self.client.patch(PROFILE, {"display_name": "Al"}, format="json")
        self.assertEqual(res.status_code, 200)

    def test_each_account_has_its_own_rate(self):
        self.assertEqual(self._attachment().status_code, 201)
        self.client.force_login(User.objects.create_user(username="bob", password="pw"))
        self.assertEqual(self._attachment().status_code, 201)


def sniff_media(head: bytes):
    # Imported here, not at the top: until spa/blocks.py exists, only these tests go red.
    from spa.blocks import sniff_media

    return sniff_media(head)


class SniffMediaTests(SimpleTestCase):
    """What a file is comes from its first bytes, never from its name or the browser's word."""

    def test_the_five_playable_kinds_are_recognised(self):
        for head, ext in (
            (MP4, ".mp4"),
            (M4A, ".m4a"),
            (WEBM, ".webm"),
            (MP3_TAGGED, ".mp3"),
            (MP3_FRAME, ".mp3"),
            (OGG_VORBIS, ".ogg"),
            (OGG_OPUS, ".ogg"),
        ):
            with self.subTest(ext=ext, head=head[:12]):
                self.assertEqual(sniff_media(head[:64]), ext)

    def test_lookalikes_and_everything_else_are_not(self):
        for name, head in (
            ("QuickTime", MOV),
            ("HEIC image", HEIC),
            ("Matroska", MKV),
            ("bare AAC", AAC_ADTS),
            ("Ogg video", OGG_THEORA),
            ("PNG", PNG),
            ("PDF", PDF),
            ("HTML", HTML),
            ("empty", b""),
        ):
            with self.subTest(name):
                self.assertIsNone(sniff_media(head[:64]))


def _media(data=MP4, name="talk.mp4", content_type="video/mp4"):
    return SimpleUploadedFile(name, data, content_type=content_type)


@tag("files")
@override_settings(**own_media_roots())
class BlockMediaUploadTests(APITestCase):
    """A media block's file: public under /media/, so it has to be what it claims, and it has to
    fit the per-file cap and the per-user quota. A replaced or dropped file leaves the disk."""

    SERIALIZER = "spa.serializers.PortfolioBlockSerializer"

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.client.force_login(self.alice)

    def _post(self, media=None, **fields):
        body = {"kind": "media", "media": media or _media(), **fields}
        return self.client.post(BLOCKS, body, format="multipart")

    def _block(self, **fields) -> PortfolioBlock:
        res = self._post(**fields)
        self.assertEqual(res.status_code, 201, res.content)
        return PortfolioBlock.objects.get(pk=res.json()["id"])

    def test_a_video_is_stored_under_a_new_name_whatever_it_was_called(self):
        block = self._block(
            media=_media(name="Bewerbung_Alice.html", content_type="text/html")
        )
        self.assertRegex(
            block.media.name, rf"^portfolio/media/{self.alice.pk}/{UUID_HEX}\.mp4$"
        )
        self.assertTrue(_under(block.media.path, settings.MEDIA_ROOT), block.media.path)
        self.assertTrue(Path(block.media.path).exists())

    def test_the_extension_is_the_one_the_bytes_prove(self):
        for data, ext in (
            (M4A, ".m4a"),
            (WEBM, ".webm"),
            (MP3_TAGGED, ".mp3"),
            (OGG_OPUS, ".ogg"),
        ):
            with self.subTest(ext=ext):
                block = self._block(media=_media(data, name="clip.mp4"))
                self.assertEqual(Path(block.media.name).suffix, ext)

    def test_what_isnt_audio_or_video_is_refused(self):
        for name, data in (
            ("PNG", PNG),
            ("HTML", HTML),
            ("QuickTime", MOV),
            ("PDF", PDF),
        ):
            with self.subTest(name):
                res = self._post(media=_media(data, name="clip.mp4"))
                self.assertEqual(res.status_code, 400, res.content)
                self.assertIn("media", res.json())
        self.assertFalse(PortfolioBlock.objects.exists())
        # This user's folder only: the class's other tests leave their files in the root.
        folder = Path(settings.MEDIA_ROOT) / "portfolio" / "media" / str(self.alice.pk)
        self.assertFalse(list(folder.glob("*")))

    def test_a_media_block_needs_its_file(self):
        res = self.client.post(
            BLOCKS, {"kind": "media", "title": "Talk"}, format="multipart"
        )
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("media", res.json())

    def test_a_poster_goes_with_it(self):
        res = self._post(image=_png())
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertIn("/media/portfolio/media/", body["media"])
        self.assertIn("/media/portfolio/blocks/", body["image"])

    def test_an_oversized_file_is_refused(self):
        with patch(f"{self.SERIALIZER}._MAX_MEDIA_BYTES", 8):
            res = self._post()
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("media", res.json())

    def test_the_quota_counts_the_users_other_files(self):
        with patch(f"{self.SERIALIZER}._MEDIA_QUOTA_BYTES", len(MP4) * 2 - 1):
            self.assertEqual(self._post().status_code, 201)
            res = self._post()
        self.assertEqual(res.status_code, 400, res.content)
        self.assertIn("media", res.json())

    def test_another_users_files_dont_count(self):
        bob = User.objects.create_user(username="bob", password="pw")
        PortfolioBlock.objects.create(user=bob, kind="media", media=_media())
        with patch(f"{self.SERIALIZER}._MEDIA_QUOTA_BYTES", len(MP4)):
            self.assertEqual(self._post().status_code, 201)

    def test_a_file_gone_from_disk_takes_no_space(self):
        block = self._block()
        Path(block.media.path).unlink()
        with patch(f"{self.SERIALIZER}._MEDIA_QUOTA_BYTES", len(MP4)):
            self.assertEqual(self._post().status_code, 201)

    def test_a_replacement_doesnt_count_the_file_it_replaces(self):
        block = self._block()
        with patch(f"{self.SERIALIZER}._MEDIA_QUOTA_BYTES", len(MP4)):
            res = self.client.patch(
                f"{BLOCKS}{block.pk}/", {"media": _media()}, format="multipart"
            )
        self.assertEqual(res.status_code, 200, res.content)

    def test_replacing_the_file_takes_the_old_one_along(self):
        block = self._block()
        old = block.media.path
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.patch(
                f"{BLOCKS}{block.pk}/", {"media": _media(WEBM)}, format="multipart"
            )
        self.assertEqual(res.status_code, 200, res.content)
        block.refresh_from_db()
        self.assertFalse(Path(old).exists(), old)
        self.assertTrue(Path(block.media.path).exists())

    def test_replacing_an_image_takes_the_old_one_along(self):
        res = self.client.post(
            BLOCKS, {"kind": "image", "image": _png()}, format="multipart"
        )
        block = PortfolioBlock.objects.get(pk=res.json()["id"])
        old = block.image.path
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.patch(
                f"{BLOCKS}{block.pk}/", {"image": _png()}, format="multipart"
            )
        self.assertEqual(res.status_code, 200, res.content)
        block.refresh_from_db()
        self.assertNotEqual(block.image.path, old)
        self.assertFalse(Path(old).exists(), old)

    def test_switching_the_kind_keeps_the_files(self):
        """A misclick on the kind loses nothing: switching back brings the file back."""
        block = self._block(image=_png())
        stored = block.media.name
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.patch(
                f"{BLOCKS}{block.pk}/",
                {"kind": "text", "body": "Now a text."},
                format="json",
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(Path(block.media.path).exists())
        self.assertTrue(Path(block.image.path).exists())
        res = self.client.patch(
            f"{BLOCKS}{block.pk}/", {"kind": "media"}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.content)
        block.refresh_from_db()
        self.assertEqual(block.media.name, stored)

    def test_a_kind_without_audio_or_video_refuses_the_file(self):
        for fields in (
            {"kind": "text", "body": "x"},
            {"kind": "image", "image": _png()},
        ):
            with self.subTest(kind=fields["kind"]):
                res = self.client.post(
                    BLOCKS, {**fields, "media": _media()}, format="multipart"
                )
                self.assertEqual(res.status_code, 400, res.content)
                self.assertIn("media", res.json())

    def test_a_plain_edit_keeps_the_files(self):
        block = self._block(image=_png())
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.patch(
                f"{BLOCKS}{block.pk}/", {"title": "My talk"}, format="json"
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(Path(block.media.path).exists())
        self.assertTrue(Path(block.image.path).exists())
