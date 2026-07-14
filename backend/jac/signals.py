"""Ingest-on-write for the vector store: any change to a career entry or snippet
queues a full vector resync for its owner (see `sync_user_vectors`).

One trigger per save is enough: the task recomputes the complete corpora and
content hashes skip unchanged rows, so over-triggering is cheap. M2M edits that
slip past post_save (a PATCH that only touches `skills`) are caught by the
query-time reconcile — the backstop, not the signals, carries correctness.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save

from jac.models import (
    Certification,
    Education,
    Job,
    Language,
    Project,
    ResumeSnippet,
    Skill,
)

logger = logging.getLogger(__name__)

_WATCHED = (Job, Project, Education, Certification, Skill, Language, ResumeSnippet)


def _enqueue(user_id: int) -> None:
    from jac.tasks import sync_user_vectors

    try:
        sync_user_vectors.delay(user_id)
    except Exception:  # noqa: BLE001 — a down broker must never break a save
        logger.warning("vector sync enqueue failed for user %s", user_id)


def queue_vector_sync(sender, instance, **kwargs):
    from vector_store import store

    if not store.is_enabled():
        return
    user_id = instance.user_id
    transaction.on_commit(lambda: _enqueue(user_id))


def connect() -> None:
    for model in _WATCHED:
        post_save.connect(
            queue_vector_sync,
            sender=model,
            dispatch_uid=f"jac.vector_sync.save.{model.__name__}",
        )
        post_delete.connect(
            queue_vector_sync,
            sender=model,
            dispatch_uid=f"jac.vector_sync.delete.{model.__name__}",
        )
