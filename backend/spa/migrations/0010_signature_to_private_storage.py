"""Move an existing signature from the public MEDIA_ROOT into private storage, under an
unguessable name. Dev data only — nothing is deployed. A row whose file is already gone stays as
it is; the owner's endpoint answers 404 for it."""

from pathlib import Path
from uuid import uuid4

from django.core.files.storage import default_storage
from django.db import migrations


def forwards(apps, schema_editor):
    UserProfile = apps.get_model("spa", "UserProfile")
    private = UserProfile._meta.get_field("signature").storage
    for profile in UserProfile.objects.exclude(signature=""):
        old = profile.signature.name
        if not default_storage.exists(old):
            continue
        name = f"signatures/{profile.user_id}/{uuid4().hex}{Path(old).suffix.lower()}"
        with default_storage.open(old, "rb") as fh:
            new = private.save(name, fh)
        UserProfile.objects.filter(pk=profile.pk).update(signature=new)
        default_storage.delete(old)


class Migration(migrations.Migration):
    dependencies = [("spa", "0009_private_uploads")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
