"""Storage for owner-only uploads, and deleting uploads when their rows go. See
lukehirsch/private_media.py for how private files are served."""

import os
import shutil
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage
from django.db import transaction
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateStorage(FileSystemStorage):
    """FileSystemStorage rooted at PRIVATE_MEDIA_ROOT, outside the public MEDIA_ROOT.

    Re-decorated with @deconstructible on purpose: the parent's decorator pins its migration path
    to FileSystemStorage, which would serialize this field as a plain public storage.

    The roots are read from settings on every access (the parent caches them and only resets
    the MEDIA_* ones), so `override_settings(PRIVATE_MEDIA_ROOT=...)` works in tests.

    `url()` is the nginx-internal path (/_private/<name>) — what X-Accel-Redirect points at. A
    browser hitting it directly gets nginx's 404: the location is `internal`."""

    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return settings.PRIVATE_MEDIA_URL


def delete_on_commit(*field_files) -> None:
    """Delete these stored files once the surrounding transaction commits: a rolled-back delete
    keeps its files. Missing files are fine (FileSystemStorage.delete ignores them).

    Robust: the rows are already gone by then, so a file that can't be removed gets logged rather
    than turning a finished delete into a 500 and skipping the callbacks behind it."""
    targets = [(f.storage, f.name) for f in field_files if f and f.name]

    def delete_targets():
        for storage, name in targets:
            storage.delete(name)

    if targets:
        transaction.on_commit(delete_targets, robust=True)


def purge_user_uploads(user_id: int) -> None:
    """Every file a user ever uploaded, including orphans no row points at anymore (a replaced
    signature or avatar). Per-user prefixes make most of this a directory delete."""
    for storage, prefix in (
        (PrivateStorage(), f"cv_attachments/{user_id}"),
        (PrivateStorage(), f"signatures/{user_id}"),
        (default_storage, f"portfolio/blocks/{user_id}"),
    ):
        shutil.rmtree(storage.path(prefix), ignore_errors=True)
    avatars = Path(default_storage.path("avatars"))
    # avatars/<id>.<ext>, plus Django's collision renames avatars/<id>_<random>.<ext>
    for avatar in (*avatars.glob(f"{user_id}.*"), *avatars.glob(f"{user_id}_*")):
        avatar.unlink(missing_ok=True)
