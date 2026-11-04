"""Site-level background jobs: sending mail, and the nightly clean-up."""

import logging
from datetime import timedelta
from smtplib import SMTPException

from celery import Task, shared_task
from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.management import call_command
from django.utils import timezone

from jac.models import DailyGenerationCount
from llm_connector.models import AiJob, LLMRequestLog

logger = logging.getLogger(__name__)

# How long a short job's row stays readable for a page that polls late.
JOB_RETENTION = timedelta(days=1)

# Mail has no status row, so its failures are counted here for the queue report. Without verification
# mail nobody can sign up, which is exactly what must not stay invisible.
MAIL_FAILED_KEY = "jac:mail:failed"
MAIL_FAILED_WINDOW_S = 7 * 24 * 60 * 60


class _CountsFailures(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        try:
            cache.add(MAIL_FAILED_KEY, 0, MAIL_FAILED_WINDOW_S)
            cache.incr(MAIL_FAILED_KEY)
        except Exception:  # a count must not turn one failure into two
            logger.warning("send_email: couldn't count a failure", exc_info=True)
        logger.error("send_email gave up: %s", exc)


@shared_task(
    base=_CountsFailures,
    autoretry_for=(SMTPException, OSError),
    retry_backoff=30,
    retry_backoff_max=15 * 60,
    max_retries=6,
)
def send_email(message: dict) -> None:
    """Send one queued mail (lukehirsch/mail.py). An SMTP hiccup retries for about half an hour."""
    alternatives = message.pop("alternatives", [])
    mail = EmailMultiAlternatives(
        connection=get_connection(settings.QUEUED_EMAIL_BACKEND), **message
    )
    for content, mimetype in alternatives:
        mail.attach_alternative(content, mimetype)
    mail.send()


@shared_task
def housekeeping() -> None:
    """What would otherwise grow forever. Also what the privacy page promises about retention."""
    now = timezone.now()
    # Finished, or unfinished for that long: it lost its task, and its poller long ago.
    AiJob.objects.filter(updated_at__lt=now - JOB_RETENTION).delete()
    LLMRequestLog.objects.filter(
        created_at__lt=now - timedelta(days=settings.LLM_LOG_RETENTION_DAYS)
    ).delete()
    # The daily cap only ever reads today's row.
    DailyGenerationCount.objects.filter(day__lt=now.date()).delete()
    call_command("clearsessions")
