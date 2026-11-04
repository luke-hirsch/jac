"""Short AI jobs: the handler registry, and how a view starts one.

A handler is a function `(job) -> dict` in the app that owns the feature (`jac/ai_jobs.py`,
`spa/ai_jobs.py`), registered with `@ai_job("kind")`. A view validates the request, calls
`start_ai_job`, and answers 202 with the row; the page polls `GET /api/llm/jobs/<id>/`.
"""

import logging
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone
from kombu.exceptions import OperationalError

from .models import AiJob

logger = logging.getLogger(__name__)


class AiJobFailed(Exception):
    """A handler's way to fail with a message the user may read."""


@dataclass(frozen=True)
class _Handler:
    fn: object
    wait_s: int | None


_HANDLERS: dict[str, _Handler] = {}


def ai_job(kind: str, *, wait_s: int | None = None):
    """Register a handler. `wait_s` is how long it may wait for the model server; a job somebody
    will soon stop polling for can say less than the default (settings.AI_JOB_WAIT_S)."""

    def register(fn):
        _HANDLERS[kind] = _Handler(fn=fn, wait_s=wait_s)
        return fn

    return register


def handler_for(kind: str) -> _Handler:
    return _HANDLERS[kind]


def start_ai_job(kind: str, *, user, payload: dict) -> AiJob:
    """Create the status row and queue it. A broker that can't take it fails the job right away
    with a message: the page still answers, and its poll reads the failure."""
    from .tasks import run_ai_job

    job = AiJob.objects.create(kind=kind, user=user, payload=payload)
    try:
        run_ai_job.apply_async(
            args=[str(job.pk)],
            expires=settings.AI_JOB_EXPIRES_S,
            # Passages and a visitor's question stay out of the worker's log lines.
            argsrepr="(job)",
        )
    except OperationalError:
        logger.exception("ai job %s: the broker refused the task", job.pk)
        AiJob.objects.filter(pk=job.pk).update(
            status=AiJob.Status.failed,
            error="The job queue isn't reachable right now — try again in a moment.",
            payload={},
            updated_at=timezone.now(),
        )
        job.refresh_from_db()
    return job


def jobs_ahead(job: AiJob) -> int:
    """How many earlier jobs are still unfinished. 0 once this one left the queue."""
    if job.status != AiJob.Status.pending:
        return 0
    return AiJob.objects.filter(
        status__in=[AiJob.Status.pending, AiJob.Status.running],
        created_at__lt=job.created_at,
    ).count()
