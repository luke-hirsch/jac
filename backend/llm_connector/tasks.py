"""The worker side of short AI jobs. Same rules as a generation run: safe to run twice, a crash
loop ends, and a model server that is off or starting is a stage, not a failure."""

import logging
from datetime import timedelta
from urllib import request

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from .base import WAITING_STAGE, LLMTransportError, LLMUnavailable
from .client import wait_for_compute
from .jobs import AiJobFailed, handler_for
from .models import AiJob
from .probe import hirschai_base_url

logger = logging.getLogger(__name__)

MAX_JOB_ATTEMPTS = 3
# One knock per this long, however many people are on their way. Less than the 20 minutes or so a wake
# keeps the tower up, so a steady stream of people keeps it awake, and a crawler can't start a new one.
PREWARM_COOLDOWN_S = 15 * 60
PREWARM_TIMEOUT_S = 5
_PREWARM_LOCK = "llm:hirschai:prewarmed"


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


@shared_task
def reap_lost_jobs() -> int:
    """Close the jobs nothing will ever finish: the task expired after a crash, or the broker lost
    it. Their page polls for a day otherwise, and they count as "ahead" for everyone behind them. A
    live job can't get this old: its hard limit is below its expiry."""
    cutoff = timezone.now() - timedelta(seconds=settings.AI_JOB_EXPIRES_S + 60)
    count = AiJob.objects.filter(
        status__in=[AiJob.Status.pending, AiJob.Status.running], updated_at__lt=cutoff
    ).update(
        status=AiJob.Status.failed,
        error="This got lost on the way — try again.",
        stage="",
        payload={},
        updated_at=timezone.now(),
    )
    if count:
        logger.warning("reap_lost_jobs: closed %s jobs", count)
    return count


def request_prewarm() -> None:
    """Somebody is about to need the model server, so ask for it ahead of their request: a cold start
    is about 3 minutes. For a person's action, never a page load: crawlers load pages, and a false wake
    costs about 20 minutes of tower time. Off without a key. It runs inside a request, so it never
    waits and never raises: the real request wakes the server anyway."""
    if not settings.HIRSCHAI_PREWARM_KEY:
        return
    try:
        if cache.add(_PREWARM_LOCK, 1, PREWARM_COOLDOWN_S):
            prewarm_model_server.apply_async(expires=60)
    except Exception:
        logger.warning("prewarm: couldn't queue the knock", exc_info=True)


@shared_task
def prewarm_model_server() -> None:
    """One knock, on its own token so the lab's usage shows what warming costs. Any request wakes the
    proxy and this token doesn't hold, so a sleeping tower answers 503 at once: the answer doesn't
    matter, and neither does no answer."""
    try:
        req = request.Request(
            hirschai_base_url() + "/v1/models",
            headers={"Authorization": f"Bearer {settings.HIRSCHAI_PREWARM_KEY}"},
        )
        with request.urlopen(req, timeout=PREWARM_TIMEOUT_S):
            pass
    except Exception:  # noqa: BLE001 — see above. The text can name the server: not worth logging
        logger.info("prewarm: no answer yet, which is normal while it wakes")
