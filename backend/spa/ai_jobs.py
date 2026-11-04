"""The queued AI jobs of the profile and the portfolio."""

from django.conf import settings

from llm_connector.conf import resolve_executor
from llm_connector.jobs import ai_job
from spa.models import PersonalityProfile
from spa.portfolio import build_intro


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


@ai_job("portfolio_intro", wait_s=settings.PORTFOLIO_INTRO_WAIT_S)
def portfolio_intro(job) -> dict:
    """`build_intro` answers '' on any failure: the page simply stays without an intro."""
    p = job.payload
    return {
        "intro": build_intro(
            job.user,
            domains=p.get("domains") or [],
            question=p.get("query") or "",
            focus=p.get("focus", "balanced"),
            tone=p.get("tone", "neutral"),
        )
    }
