"""Celery tasks for async generation.

`generate_run` owns the run lifecycle and streams progress to the `gen_<run_id>` channel group;
the WebSocket consumer forwards those to the browser. THIS GUIDE ships a stub body — guide 2 swaps
it for the real CV + cover-letter pipeline. The lifecycle / event contract here is the part the
consumer + frontend depend on, so keep it stable.
"""

import logging

from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer
from llm_connector.conf import get_alias_strength

from jac.cover_letter import CoverLetter
from jac.cv import CV
from jac.generation_result import serialize_cv_selection
from jac.llm_prompts import AddressExtract
from jac.models import GenerationRun, JobPostAddress

logger = logging.getLogger(__name__)


_ADDRESS_FIELDS = (
    "company",
    "contact_name",
    "street",
    "address_line2",
    "zip",
    "city",
    "country",
    "email",
    "phone",
)


def publish_event(run_id: int, payload: dict) -> None:
    """Fan a progress/terminal event out to the run's channel group. No-op if no layer."""
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        f"gen_{run_id}", {"type": "gen.event", "payload": payload}
    )


def _progress(run: GenerationRun, stage: str) -> None:
    run.stage = stage
    run.save(update_fields=["stage", "updated_at"])
    publish_event(run.pk, {"event": "progress", "status": run.status, "stage": stage})


@shared_task
def generate_run(run_id: int) -> None:
    run = GenerationRun.objects.filter(pk=run_id).first()
    if run is None:
        logger.warning("generate_run: no run %s", run_id)
        return

    run.status = GenerationRun.Status.running
    run.save(update_fields=["status", "updated_at"])
    publish_event(run.pk, {"event": "progress", "status": run.status, "stage": ""})

    try:
        user = run.user
        alias = run.alias or "default"
        grade = run.grade or get_alias_strength(alias, user=user)

        # 1. Tailor the CV.
        _progress(run, "filtering CV")
        cv = CV(
            user_pk=user.pk,
            domains=run.domains or None,
            started=run.started,
            ended=run.ended,
            min_skill_proficiency=run.min_skill_proficiency or None,
        )
        cv.apply_selection(cv.filter_cv(run.posting_text, grade=grade, alias=alias))

        # 2. Extract the recipient address; refresh the persisted JobPosting.
        _progress(run, "reading posting")
        extracted = AddressExtract(run.posting_text, alias=alias, user=user).extract()
        jp = run.job_posting
        if jp is not None:
            jp.title = extracted.get("title", "") or jp.title
            jp.language = extracted.get("language", "en") or "en"
            jp.save(update_fields=["title", "language", "updated_at"])
        addr = JobPostAddress(**{f: extracted.get(f, "") for f in _ADDRESS_FIELDS})

        # 3. Build the cover letter.
        _progress(
            run, "researching company" if run.personal_paragraph else "writing letter"
        )
        letter = CoverLetter(
            user,
            jp,
            cv,
            address=addr,
            grade=grade,
            alias=alias,
            max_body_snippets=run.max_body_snippets,
            verify_grounding=run.verify_grounding,
            verifier_alias=run.verifier_alias or None,
            personal_paragraph=run.personal_paragraph,
            research_alias=run.research_alias or None,
        ).build()

        run.result = {
            "meta": {"grade": grade, "alias": alias},
            "cv": serialize_cv_selection(cv),
            "cover_letter": letter,
        }
        run.status = GenerationRun.Status.done
        run.stage = "done"
        run.save(update_fields=["result", "status", "stage", "updated_at"])
        publish_event(
            run.pk, {"event": "done", "status": run.status, "result": run.result}
        )

    except Exception as exc:  # noqa: BLE001 — surface any pipeline failure to the client
        logger.exception("generate_run %s failed", run_id)
        run.status = GenerationRun.Status.failed
        run.error = str(exc)
        run.save(update_fields=["status", "error", "updated_at"])
        publish_event(
            run.pk, {"event": "failed", "status": run.status, "error": run.error}
        )
