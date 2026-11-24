"""The queued AI jobs of the profile and the portfolio."""

import logging

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

from llm_connector.base import LLMTransportError, LLMUnavailable
from llm_connector.conf import resolve_executor
from llm_connector.jobs import ai_job, publish_partial
from spa.models import PersonalityProfile
from spa.portfolio import build_intro, rank_for_query

logger = logging.getLogger(__name__)


def _executor(job):
    return resolve_executor(
        job.user, job.payload.get("provider", ""), job.payload.get("model", "")
    )


@ai_job("personality_dossier")
def personality_dossier(job) -> dict:
    profile = PersonalityProfile.objects.get(user=job.user)
    profile.dossier_built_at = None  # force ensure_dossier to re-distil
    return {"dossier": profile.ensure_dossier(_executor(job))}


@ai_job("style_dossier")
def style_dossier(job) -> dict:
    profile = PersonalityProfile.objects.get(user=job.user)
    profile.style_built_at = None  # force ensure_style_dossier to re-distil
    return {"dossier": profile.ensure_style_dossier(_executor(job))}


@ai_job("portfolio_personalise", wait_s=settings.PORTFOLIO_PERSONALISE_WAIT_S)
def portfolio_personalise(job) -> dict:
    """The AI half of a questionnaire visit, in one job: sort the entries by what the visitor wrote
    (when they wrote anything), then write the welcome. It rides out a sleeping model server like
    every queued job, and the ranking is published as soon as it's there, so the waiting page can
    tick it off.

    `ranked` is None when there was nothing to rank by or the ranking broke; `intro` is '' when
    the welcome failed (`build_intro` never raises). A model server that's still gone after the
    wait fails the whole job: the welcome would only wait for the same server."""
    p = job.payload
    query = p.get("query") or ""
    domains = p.get("domains") or []
    ranked = None
    if query:
        try:
            ranked = rank_for_query(job.user, query, domains)
        except (LLMTransportError, LLMUnavailable, SoftTimeLimitExceeded):
            raise
        except Exception:  # every AI feature has a path without AI
            logger.warning("portfolio rank failed", exc_info=True)
        publish_partial(job, {"ranked": ranked, "intro": ""})
    intro = build_intro(
        job.user,
        domains=domains,
        question=query,
        focus=p.get("focus", "balanced"),
        tone=p.get("tone", "neutral"),
    )
    return {"ranked": ranked, "intro": intro}
