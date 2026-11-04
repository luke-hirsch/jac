"""Owner-only uploads: the response that hands one out + the serializer fields that point at it.

nginx serves MEDIA_ROOT publicly under /media/ (avatars, portfolio images: public by design).
Certificates and signatures live in PrivateStorage instead (lukehirsch/storage.py), which no
public location reaches. A view checks ownership, then answers with `private_file_response`.
"""

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.utils.http import content_disposition_header
from rest_framework import serializers


def private_file_response(field_file, *, content_type: str, filename: str):
    """Hand one private file to its (already authorised) owner.

    Prod (PRIVATE_MEDIA_ACCEL): an empty response with X-Accel-Redirect — nginx streams the file
    from its `internal` /_private/ location. Dev: Django streams it. Either way the type is
    explicit and `nosniff` stops a browser from guessing its way into HTML."""
    if not field_file or not field_file.storage.exists(field_file.name):
        raise Http404
    if settings.PRIVATE_MEDIA_ACCEL:
        response = HttpResponse(content_type=content_type)
        response["X-Accel-Redirect"] = field_file.storage.url(field_file.name)
    else:
        response = FileResponse(
            field_file.storage.open(field_file.name, "rb"), content_type=content_type
        )
    response["Content-Disposition"] = content_disposition_header(False, filename)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


class PrivateFileField(serializers.FileField):
    """Uploads like a FileField; reads back as the owner-checked download path, never a storage
    URL. `path_for(field_file)` builds that path — the field doesn't know the view's URL."""

    def __init__(self, *, path_for, **kwargs):
        self.path_for = path_for
        super().__init__(**kwargs)

    def to_representation(self, value):
        if not value:
            return None
        return self.path_for(value)


class PrivateImageField(PrivateFileField, serializers.ImageField):
    """PrivateFileField with ImageField's Pillow validation on upload."""
