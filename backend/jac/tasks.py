"""Celery tasks for async generation.

`generate_run` owns the run lifecycle and streams progress to the `gen_<run_id>` channel
group; the WebSocket consumer forwards those to the browser. The lifecycle / event contract
(`snapshot`/`progress`/`done`/`failed`) is the part the consumer + frontend depend on, so keep
it stable. Ownership and the posting are derived through `run.job_application`.

Lifecycle rules (cancel-safe):
  - the task only *claims* a `pending` run (conditional update) — a run cancelled while
    queued, or one another worker already handled, is skipped;
  - terminal writes (`done`/`failed`) only land while the run is still `running`, so a
    user cancel can never be overwritten by a slow task finishing afterwards;
  - transient LLM transport failures retry once (see `llm_connector.client.retry_reporter`)
    and surface as a progress event while they do.
"""

import logging

from asgiref.sync import async_to_sync
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from channels.layers import get_channel_layer
from django.utils import timezone
from django.utils.dateparse import parse_date
from llm_connector.base import LLMTransportError
from llm_connector.client import retry_reporter
from llm_connector.conf import get_alias_strength, pick_alias

from jac.cover_letter import CoverLetter, editable_body
from jac.cv import CV
from jac.generation_result import serialize_cv_selection
from jac.llm_prompts import AddressExtract
from jac.models import GenerationRun, JobPostAddress

logger = logging.getLogger(__name__)

# A task that no worker picked up within this window is dropped at delivery instead of
# firing hours later when a worker finally comes back (the run then reads as stale-pending
# in the UI and can be cancelled from there).
GENERATION_EXPIRES_S = 15 * 60

# Soft cap for one whole generation; the hard CELERY_TASK_TIME_LIMIT (30 min) stays the
# backstop. On the soft limit the run fails loudly instead of dying without a trace.
GENERATION_SOFT_TIME_LIMIT_S = 25 * 60


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


def _parse_deadline(value: str):
    """Best-effort `date` from the extractor's `deadline` line — we instruct ISO
    (YYYY-MM-DD), so `parse_date` handles the happy path and returns None on anything else."""
    value = (value or "").strip()
    return parse_date(value) if value else None


def publish_event(run_id: int, payload: dict) -> None:
    """Fan a progress/terminal event out to the run's channel group. No-op if no layer."""
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        f"gen_{run_id}", {"type": "gen.event", "payload": payload}
    )


def _progress(run: GenerationRun, stage: str) -> None:
    # Conditional on `running` so a cancelled run stops emitting progress mid-pipeline.
    run.stage = stage
    updated = GenerationRun.objects.filter(
        pk=run.pk, status=GenerationRun.Status.running
    ).update(stage=stage, updated_at=timezone.now())
    if updated:
        publish_event(
            run.pk, {"event": "progress", "status": run.status, "stage": stage}
        )


def _fail(run: GenerationRun, error: str) -> None:
    """Mark a still-running run failed and publish the terminal event. A run already
    terminal (e.g. cancelled by the user) is left untouched."""
    updated = GenerationRun.objects.filter(
        pk=run.pk, status=GenerationRun.Status.running
    ).update(status=GenerationRun.Status.failed, error=error, updated_at=timezone.now())
    if updated:
        publish_event(
            run.pk,
            {"event": "failed", "status": GenerationRun.Status.failed, "error": error},
        )


@shared_task(soft_time_limit=GENERATION_SOFT_TIME_LIMIT_S)
def generate_run(run_id: int) -> None:
    run = (
        GenerationRun.objects.select_related(
            "job_application__user", "job_application__posting"
        )
        .filter(pk=run_id)
        .first()
    )
    if run is None:
        logger.warning("generate_run: no run %s", run_id)
        return

    # Claim the run: only a pending one is ours to execute. Anything else was cancelled
    # while queued, already ran, or is owned by another worker — skip silently.
    claimed = GenerationRun.objects.filter(
        pk=run_id, status=GenerationRun.Status.pending
    ).update(status=GenerationRun.Status.running, updated_at=timezone.now())
    if not claimed:
        logger.info(
            "generate_run %s: status %r not claimable — skipping", run_id, run.status
        )
        return
    run.status = GenerationRun.Status.running
    publish_event(run.pk, {"event": "progress", "status": run.status, "stage": ""})

    def notify_retry(operation: str, delay_s: float, error: str) -> None:
        # Transient, not persisted: the DB keeps the real stage; the browser just gets
        # told why nothing is moving right now.
        publish_event(
            run.pk,
            {
                "event": "progress",
                "status": run.status,
                "stage": f"LLM {operation} failed — retrying in {delay_s:.0f}s",
            },
        )

    try:
        with retry_reporter(notify_retry):
            application = run.job_application
            user = application.user
            jp = application.posting
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
            cv.apply_selection(cv.filter_cv(jp.posting_text, grade=grade, alias=alias))

            # 2. Extract the recipient address; refresh the persisted JobPosting.
            # Support rung: runs on the user's pin for its preferred tier when present
            # (a strong run shouldn't spend its expensive model on field extraction);
            # a light run never routes onto a paid pin.
            _progress(run, "reading posting")
            extract_alias = pick_alias(
                AddressExtract.PREFERRED_GRADE,
                fallback=alias,
                user=user,
                free_only=grade == "light",
            )
            extracted = AddressExtract(
                jp.posting_text, alias=extract_alias, user=user
            ).extract()
            jp.title = extracted.get("title", "") or jp.title
            jp.language = extracted.get("language", "en") or "en"
            jp.save(update_fields=["title", "language", "updated_at"])
            addr, _ = JobPostAddress.objects.update_or_create(
                job_posting=jp,
                defaults={f: extracted.get(f, "") for f in _ADDRESS_FIELDS},
            )
            deadline = _parse_deadline(extracted.get("deadline", ""))
            if deadline and application.deadline is None:
                application.deadline = deadline
                application.save(update_fields=["deadline", "updated_at"])

            # 3. Build the cover letter.
            _progress(
                run,
                "researching company" if run.personal_paragraph else "writing letter",
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

        result = {
            "meta": {"grade": grade, "alias": alias},
            "cv": serialize_cv_selection(cv),
            "cover_letter": letter,
        }
        # Terminal write, conditional on still running: a cancel that landed mid-pipeline
        # wins, and its result is discarded.
        finished = GenerationRun.objects.filter(
            pk=run.pk, status=GenerationRun.Status.running
        ).update(
            result=result,
            status=GenerationRun.Status.done,
            stage="done",
            updated_at=timezone.now(),
        )
        if not finished:
            logger.info(
                "generate_run %s: cancelled while running — result discarded", run_id
            )
            return
        run.result = result
        run.status = GenerationRun.Status.done

        # Auto-fill the application only while it's still untouched; afterwards the user
        # applies a run's result explicitly from the SPA, so re-runs never clobber edits.
        if not application.cv_content and not application.cover_letter:
            application.cv_content = result["cv"]
            application.cover_letter = editable_body(letter)
            application.letter_meta = {
                k: letter[k]
                for k in (
                    "language",
                    "subject",
                    "salutation",
                    "date",
                    "closing",
                    "sender",
                    "recipient",
                )
                if k in letter
            }
            application.save(
                update_fields=[
                    "cv_content",
                    "cover_letter",
                    "letter_meta",
                    "updated_at",
                ]
            )

        publish_event(run.pk, {"event": "done", "status": run.status, "result": result})

    except SoftTimeLimitExceeded:
        logger.warning("generate_run %s hit the soft time limit", run_id)
        _fail(
            run,
            "generation timed out — the model took too long; "
            "try again or pick a lighter grade",
        )
    except LLMTransportError as exc:
        logger.exception("generate_run %s: LLM unreachable", run_id)
        _fail(
            run,
            f"could not reach the language model ({exc}) — "
            "check that the model server is running",
        )
    except Exception as exc:  # noqa: BLE001 — surface any pipeline failure to the client
        logger.exception("generate_run %s failed", run_id)
        _fail(run, str(exc))


@shared_task
def sync_user_vectors(user_id: int) -> None:
    """Ingest-on-write for the vector store: refresh one user's corpora (CV
    entries + snippets). Runs on the FULL sets, so orphan deletion is safe here —
    the query-time reconcile only ever upserts. No-op when the store is off;
    reconcile logs its own failures, so a broken store never errors the task."""
    from vector_store import store

    from jac import vectors

    if not store.is_enabled():
        return
    alias = vectors.sync_alias(user_id)
    vectors.reconcile(
        user_id, alias, vectors.DOC_CV, vectors.cv_corpus(user_id), delete_orphans=True
    )
    vectors.reconcile(
        user_id,
        alias,
        vectors.DOC_SNIPPET,
        vectors.snippet_corpus(user_id),
        delete_orphans=True,
    )
