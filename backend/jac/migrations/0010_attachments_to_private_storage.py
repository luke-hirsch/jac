"""Move CvAttachment files from the public MEDIA_ROOT into private storage, under unguessable
names. Dev data only — nothing is deployed. A row whose file is already gone stays as it is; the
download view answers 404 for it."""

from uuid import uuid4

from django.core.files.storage import default_storage
from django.db import migrations


def forwards(apps, schema_editor):
    CvAttachment = apps.get_model("jac", "CvAttachment")
    private = CvAttachment._meta.get_field("file").storage
    for att in CvAttachment.objects.exclude(file=""):
        old = att.file.name
        if not default_storage.exists(old):
            continue
        with default_storage.open(old, "rb") as fh:
            new = private.save(f"cv_attachments/{att.user_id}/{uuid4().hex}.pdf", fh)
        CvAttachment.objects.filter(pk=att.pk).update(file=new)
        default_storage.delete(old)


class Migration(migrations.Migration):
    dependencies = [("jac", "0009_private_uploads")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
