"""The worker side of short AI jobs. Same rules as a generation run: safe to run twice, a crash
loop ends, and a model server that is off or starting is a stage, not a failure."""

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .base import WAITING_STAGE, LLMTransportError, LLMUnavailable
from .client import wait_for_compute
from .jobs import AiJobFailed, handler_for
from .models import AiJob

logger = logging.getLogger(__name__)

MAX_JOB_ATTEMPTS = 3


def _stage(job_id: str, stage: str) -> None:
    AiJob.objects.filter(pk=job_id, status=AiJob.Status.running).update(
        stage=stage, updated_at=timezone.now()
    )


def _finish(job_id: str, status: str, *, result=None, error: str = "") -> None:
    """Terminal write. The input goes with it: a passage or a question has no reason to stay."""
    AiJob.objects.filter(pk=job_id, status=AiJob.Status.running).update(
        status=status,
        result=result,
        error=error,
        stage="",
        payload={},
        updated_at=timezone.now(),
    )


@shared_task(
    soft_time_limit=settings.AI_JOB_SOFT_LIMIT_S,
    time_limit=settings.AI_JOB_SOFT_LIMIT_S + 60,
)
def run_ai_job(job_id: str) -> None:
    job = AiJob.objects.filter(pk=job_id).select_related("user").first()
    if job is None:
        return
    claimed = AiJob.objects.filter(
        pk=job_id,
        status__in=[AiJob.Status.pending, AiJob.Status.running],
        attempts__lt=MAX_JOB_ATTEMPTS,
    ).update(
        status=AiJob.Status.running,
        attempts=F("attempts") + 1,
        stage="",
        updated_at=timezone.now(),
    )
    if not claimed:
        # Still `running` here means out of attempts; anything finished is left alone.
        _finish(job_id, AiJob.Status.failed, error="This job kept crashing the worker.")
        return

    handler = handler_for(job.kind)
    budget = settings.AI_JOB_WAIT_S if handler.wait_s is None else handler.wait_s
    try:
        with wait_for_compute(
            on_wait=lambda delay, error: _stage(job_id, WAITING_STAGE),
            on_resume=lambda: _stage(job_id, ""),
            budget_s=budget,
        ):
            result = handler.fn(job)
    except AiJobFailed as exc:
        _finish(job_id, AiJob.Status.failed, error=str(exc))
    except SoftTimeLimitExceeded:
        logger.warning("ai job %s hit the soft time limit", job_id)
        _finish(job_id, AiJob.Status.failed, error="This took too long — try again.")
    except (LLMTransportError, LLMUnavailable) as exc:
        logger.warning("ai job %s: LLM unavailable: %s", job_id, exc)
        _finish(
            job_id,
            AiJob.Status.failed,
            error="The AI isn't available right now — try again later.",
        )
    except Exception:
        # The exception text can name the model server's address: log it, don't show it.
        logger.exception("ai job %s failed", job_id)
        _finish(job_id, AiJob.Status.failed, error="Something went wrong — try again.")
    else:
        _finish(job_id, AiJob.Status.done, result=result)
