"""Which queue a run goes to, how far back it stands, and the report health checks read.

The app reports its queues itself, because a failed job shows no error page: unless something
counts them, failures on a queue stay invisible. Nothing here is specific to where it's hosted.
"""

import logging
import sys

import redis
from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from jac.models import DailyGenerationCount, GenerationRun
from llm_connector.conf import HIRSCHAI_PROVIDER
from llm_connector.models import AiJob

logger = logging.getLogger(__name__)


def queue_for(provider: str) -> str:
    """HirschAI runs share the model server's single lane; a commercial run needs no model server,
    so it never waits behind one."""
    return (
        settings.QUEUE_AI if provider == HIRSCHAI_PROVIDER else settings.QUEUE_CLOUD
    )


def _same_lane(queryset, provider: str):
    if provider == HIRSCHAI_PROVIDER:
        return queryset.filter(provider=HIRSCHAI_PROVIDER)
    return queryset.exclude(provider=HIRSCHAI_PROVIDER)


def runs_ahead(run: GenerationRun) -> int:
    """How many runs stand before this one, whoever's they are. Read from the status rows, so it
    costs the broker nothing. 0 for a run that's no longer waiting."""
    if run.status != GenerationRun.Status.pending:
        return 0
    return _same_lane(
        GenerationRun.objects.filter(
            status__in=[GenerationRun.Status.pending, GenerationRun.Status.running],
            created_at__lt=run.created_at,
        ),
        run.provider,
    ).count()


def daily_generation_cap() -> int | None:
    """The `generation` throttle's number, as the one daily budget. None = no cap configured."""
    rate = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"].get("generation")
    return int(rate.split("/")[0]) if rate else None


def generation_budget_left(user) -> int:
    """Runs this user may still start today, however they get started: the throttle alone only
    sees the generations endpoint, so an auto-run per new application or a batch would walk around
    it. Abuse control on an open-signup site is this number, not the signup door. Read from the
    counter, not the run rows: those leave with their application, the budget doesn't come back."""
    cap = daily_generation_cap()
    if cap is None:
        return sys.maxsize
    today = DailyGenerationCount.objects.filter(
        user=user, day=timezone.now().date()
    ).first()
    return max(cap - (today.count if today else 0), 0)


def count_generations(user, runs: int = 1) -> None:
    """Spend today's budget. Every place that creates a run calls this, in the run's transaction."""
    DailyGenerationCount.objects.update_or_create(
        user=user,
        day=timezone.now().date(),
        defaults={"count": F("count") + runs},
        create_defaults={"count": runs},
    )


def _waiting(queues: list[str]) -> dict[str, int | None]:
    try:
        broker = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        return {queue: broker.llen(queue) for queue in queues}
    except redis.RedisError:
        logger.warning("queue report: broker unreachable", exc_info=True)
        return dict.fromkeys(queues)


def _failed(provider_filter) -> int:
    return (
        provider_filter(GenerationRun.objects.filter(status=GenerationRun.Status.failed))
        .exclude(error=GenerationRun.CANCELLED_ERROR)
        .count()
    )


def queue_report() -> dict:
    """{queue: {waiting, failed}}. `waiting` is null when the broker can't be reached. `failed`
    counts every failed job still on record, leaving out user cancels (stored as failed too); mail
    has no rows, so its number is a counter over about a week."""
    waiting = _waiting(
        [
            settings.QUEUE_AI,
            settings.QUEUE_CLOUD,
            settings.QUEUE_QUICK,
            settings.QUEUE_MAIL,
        ]
    )
    return {
        settings.QUEUE_AI: {
            "waiting": waiting[settings.QUEUE_AI],
            "failed": _failed(lambda q: q.filter(provider=HIRSCHAI_PROVIDER)),
        },
        settings.QUEUE_CLOUD: {
            "waiting": waiting[settings.QUEUE_CLOUD],
            "failed": _failed(lambda q: q.exclude(provider=HIRSCHAI_PROVIDER)),
        },
        settings.QUEUE_QUICK: {
            "waiting": waiting[settings.QUEUE_QUICK],
            "failed": AiJob.objects.filter(status=AiJob.Status.failed).count(),
        },
        settings.QUEUE_MAIL: {
            "waiting": waiting[settings.QUEUE_MAIL],
            "failed": cache.get("jac:mail:failed", 0),
        },
    }
