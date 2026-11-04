"""Celery tasks for async generation.

`generate_run` owns the run lifecycle and streams progress to the `gen_<run_id>` channel
group; the WebSocket consumer forwards those to the browser. The lifecycle / event contract
(`snapshot`/`progress`/`done`/`failed`) is the part the consumer + frontend depend on, so keep
it stable. Ownership and the posting are derived through `run.job_application`.

Lifecycle rules (cancel-safe, and safe to run twice):
  - the task *claims* a `pending` run, or a `running` one the broker handed out again after a
    worker died under it (conditional update) — a run cancelled while queued, or one already
    finished, is skipped. A cancel writes `failed`, so it still wins;
  - every claim counts, so a run that keeps killing its worker fails instead of looping;
  - terminal writes (`done`/`failed`) only land while the run is still `running`, so a
    user cancel can never be overwritten by a slow task finishing afterwards;
  - a model server that is off or starting is normal: the run holds its slot in the model's
    single lane and says "waiting for compute" (see `llm_connector.client.wait_for_compute`).
    A commercial provider's transport failure retries once (`retry_reporter`). Both surface as
    progress events while they last.
"""

import logging
from datetime import timedelta

from asgiref.sync import async_to_sync
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_date

from jac.cover_letter import CoverLetter, editable_body, letter_meta
from jac.cv import CV
from jac.filter import GenerationError
from jac.generation_result import serialize_cv_selection
from jac.llm_prompts import AddressExtract
from jac.models import GenerationRun, JobApplication, JobPostAddress, JobPosting, Mode
from llm_connector.base import WAITING_STAGE, LLMTransportError, LLMUnavailable
from llm_connector.client import retry_reporter, wait_for_compute
from llm_connector.executor import Executor

logger = logging.getLogger(__name__)

# Soft cap for one whole generation, including any wait for compute; the hard
# CELERY_TASK_TIME_LIMIT stays the backstop. On the soft limit the run fails loudly instead of
# dying without a trace.
GENERATION_SOFT_TIME_LIMIT_S = settings.CELERY_TASK_SOFT_TIME_LIMIT

# A run claimed this often has killed its worker each time: stop handing it out.
MAX_RUN_ATTEMPTS = 3

# Grace on top of the broker's own timing before a row counts as stuck (reap_stuck_runs).
REAP_MARGIN_S = 5 * 60

# One mail lists at most this many runs.
NOTIFY_MAX_LISTED = 20


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


def _clipped(model, field: str, value: str) -> str:
    """LLM output for a bounded column: Postgres refuses what doesn't fit, and one chatty address
    line must not cost the run."""
    return value[: model._meta.get_field(field).max_length]


def _parse_deadline(value: str):
    """Best-effort `date` from the extractor's `deadline` line — we instruct ISO
    (YYYY-MM-DD), so `parse_date` handles the happy path and returns None on anything else,
    but it raises on a well-formed impossible date (2026-06-31). Neither may cost the run."""
    value = (value or "").strip()
    try:
        return parse_date(value) if value else None
    except ValueError:
        return None


def publish_event(run_id: int, payload: dict) -> None:
    """Fan a progress/terminal event out to the run's channel group. No-op if no layer. A lost
    push never breaks a run or a cancel: the row holds the truth, and a socket re-reads it."""
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(
            f"gen_{run_id}", {"type": "gen.event", "payload": payload}
        )
    except Exception:
        logger.warning("publish_event: run %s", run_id, exc_info=True)


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
        notify_when_drained(run.user.pk)


def notify_when_drained(user_id: int) -> None:
    """Queue the "your runs are done" check. Never lets a mail problem touch the run."""
    try:
        notify_run_results.delay(user_id)
    except Exception:
        logger.warning("notify_run_results: enqueue failed", exc_info=True)


def _claim(run_id: int) -> bool:
    """`pending`, or `running` when a worker died under it and the broker handed it out again."""
    return bool(
        GenerationRun.objects.filter(
            pk=run_id,
            status__in=[GenerationRun.Status.pending, GenerationRun.Status.running],
            attempts__lt=MAX_RUN_ATTEMPTS,
        ).update(
            status=GenerationRun.Status.running,
            attempts=F("attempts") + 1,
            stage="",
            updated_at=timezone.now(),
        )
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

    if not _claim(run_id):
        logger.info(
            "generate_run %s: status %r not claimable — skipping", run_id, run.status
        )
        # Still `running` here means out of attempts; anything finished is left alone.
        _fail(run, "this run kept crashing the worker — try again or shorten the posting")
        return
    run.status = GenerationRun.Status.running
    publish_event(run.pk, {"event": "progress", "status": run.status, "stage": ""})
    if run.mode == Mode.manual:
        _fail(
            run, "manual mode never generates — build the application content by hand"
        )
        return

    def notify_retry(operation: str, delay_s: float, error: str) -> None:
        publish_event(
            run.pk,
            {
                "event": "progress",
                "status": run.status,
                "stage": f"LLM {operation} failed — retrying in {delay_s:.0f}s",
            },
        )

    stage_before_wait = {"stage": ""}

    def on_wait(delay_s: float, error: str) -> None:
        if run.stage != WAITING_STAGE:
            stage_before_wait["stage"] = run.stage
            _progress(run, WAITING_STAGE)

    def on_resume() -> None:
        _progress(run, stage_before_wait["stage"])

    try:
        with (
            retry_reporter(notify_retry),
            wait_for_compute(on_wait=on_wait, on_resume=on_resume),
        ):
            application = run.job_application
            user = application.user
            jp = application.posting
            executor = Executor(
                run.provider or "ollama", run.model or None, user, run.params or None
            )
            mode = run.mode or Mode.standard

            # 1. Tailor the CV.
            _progress(run, "filtering CV")
            cv = CV(
                user_pk=user.pk,
                domains=run.domains or None,
                started=run.started,
                ended=run.ended,
                min_skill_proficiency=run.min_skill_proficiency or None,
            )
            cv.apply_selection(
                cv.filter_cv(
                    jp.posting_text,
                    mode,
                    executor,
                    pinned=set(application.pinned_entries or []),
                )
            )

            # 2. Extract the recipient address; refresh the persisted JobPosting.
            # Support rung: runs on the user's pin for its preferred tier when present
            # (a strong run shouldn't spend its expensive model on field extraction);
            # a light run never routes onto a paid pin.
            _progress(run, "reading posting")

            extracted = AddressExtract(jp.posting_text, executor=executor).extract()
            jp.title = _clipped(JobPosting, "title", extracted.get("title", "")) or jp.title
            jp.language = _clipped(JobPosting, "language", extracted.get("language", "en")) or "en"
            jp.save(update_fields=["title", "language", "updated_at"])
            addr, _ = JobPostAddress.objects.update_or_create(
                job_posting=jp,
                defaults={
                    f: _clipped(JobPostAddress, f, extracted.get(f, "")) for f in _ADDRESS_FIELDS
                },
            )
            deadline = _parse_deadline(extracted.get("deadline", ""))
            if deadline:
                # Asked of the row, not of the snapshot: the wait for compute can be long, and
                # the user may have set one meanwhile.
                JobApplication.objects.filter(
                    pk=application.pk, deadline__isnull=True
                ).update(deadline=deadline, updated_at=timezone.now())

            # 3. Build the cover letter.
            _progress(run, "writing letter")
            letter = CoverLetter(
                user,
                jp,
                cv,
                address=addr,
                mode=mode,
                executor=executor,
                tone=run.letter_tone,
                focus=run.letter_focus,
            ).build()

        result = {
            "meta": {
                "mode": mode,
                "provider": executor.provider,
                "model": executor.model or "",
            },
            "cv": serialize_cv_selection(cv),
            "cover_letter": letter,
            "params": run.params or {},
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
        run.result = result  # type: ignore
        run.status = GenerationRun.Status.done

        # Auto-fill the application only while it's still untouched; afterwards the user
        # applies a run's result explicitly from the SPA, so re-runs never clobber edits.
        # Asked of the row, not of the snapshot from the task's start: the user may have edited
        # during the wait, and an overlapping run may have filled it first. Empty = the field
        # defaults; letter_meta can't count, the editor's Save writes normalised defaults to it.
        JobApplication.objects.filter(
            pk=application.pk, cv_content={}, cover_letter=""
        ).update(
            cv_content=result["cv"],
            cover_letter=editable_body(letter),
            letter_meta=letter_meta(letter),
            updated_at=timezone.now(),
        )

        publish_event(run.pk, {"event": "done", "status": run.status, "result": result})
        notify_when_drained(user.pk)

    except SoftTimeLimitExceeded:
        logger.warning("generate_run %s hit the soft time limit", run_id)
        _fail(
            run,
            "generation timed out — the model took too long; "
            "try again or pick a lighter grade",
        )
    except (LLMTransportError, LLMUnavailable) as exc:
        logger.warning("generate_run %s: LLM unavailable: %s", run_id, exc)
        _fail(
            run,
            "the language model isn't available right now — "
            "try again later, or pick another provider",
        )
    except GenerationError as exc:
        logger.warning("generate_run %s: %s", run_id, exc)
        _fail(run, str(exc))
    except Exception:
        # The exception text can name the model server's address: log it, don't show it.
        logger.exception("generate_run %s failed", run_id)
        _fail(run, "generation failed unexpectedly — try again")


@shared_task
def sync_user_vectors(user_id: int) -> None:
    """Ingest-on-write for the vector store: refresh one user's corpora. Runs on the FULL sets, so orphan deletion is safe here —
    the query-time reconcile only ever upserts. No-op when the store is off;
    reconcile logs its own failures, so a broken store never errors the task."""
    from jac import vectors
    from vector_store import store

    if not store.is_enabled():
        return

    vectors.reconcile(
        user_id, vectors.DOC_CV, vectors.cv_corpus(user_id), delete_orphans=True
    )


def _notification_mail(runs: list[GenerationRun]) -> tuple[str, str]:
    done = [r for r in runs if r.status == GenerationRun.Status.done]
    failed = [r for r in runs if r.status == GenerationRun.Status.failed]
    if len(runs) == 1:
        subject = "Your CV is ready" if done else "Your generation failed"
    elif not failed:
        subject = f"Your {len(done)} CVs are ready"
    elif not done:
        subject = f"Your {len(failed)} generations failed"
    else:
        subject = f"{len(done)} CVs ready, {len(failed)} failed"
    base = settings.FRONTEND_URL.rstrip("/")
    lines = [
        f"{'Ready' if r.status == GenerationRun.Status.done else 'Failed'}: "
        f"{r.posting.title or 'Untitled posting'}\n"
        f"{base}/applications/{r.job_application_id}"
        for r in runs[:NOTIFY_MAX_LISTED]
    ]
    if len(runs) > NOTIFY_MAX_LISTED:
        lines.append(f"…and {len(runs) - NOTIFY_MAX_LISTED} more.")
    footer = (
        "You get this mail because a run took long enough for you to have left. "
        "Turn it off under Account → Profile."
    )
    return subject, "\n\n".join([*lines, footer])


@shared_task
def notify_run_results(user_id: int) -> None:
    """One mail when a user's queue drains, however many runs that was: a batch of five is one
    mail, not five. Runs the user cancelled, and short ones they surely watched, stay silent. The
    conditional stamp stops a second worker from sending the same news twice."""
    user_runs = GenerationRun.objects.filter(job_application__user_id=user_id)
    active = (GenerationRun.Status.pending, GenerationRun.Status.running)
    if user_runs.filter(status__in=active).exists():
        return
    unsent = user_runs.filter(notified_at__isnull=True).select_related(
        "job_application__posting", "job_application__user__profile"
    )
    runs = list(unsent.order_by("created_at"))
    if not runs:
        return
    user = runs[0].job_application.user
    long_enough = settings.RUN_NOTIFY_AFTER_S
    due = [
        r
        for r in runs
        if r.error != GenerationRun.CANCELLED_ERROR
        and (r.updated_at - r.created_at).total_seconds() >= long_enough
    ]
    stamped = unsent.filter(pk__in=[r.pk for r in runs]).update(
        notified_at=timezone.now()
    )
    if not stamped or not due or not user.email or not user.profile.email_reminders:
        return
    subject, body = _notification_mail(due)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])


@shared_task
def reap_stuck_runs() -> int:
    """Close the rows nothing will ever finish: a queued task that expired, or a run whose worker
    died and whose task the broker has given up on (Valkey lost it, or a hard kill). The waits are
    longer than anything that can still happen to them, so a live run is never touched."""
    now = timezone.now()
    margin = timedelta(seconds=REAP_MARGIN_S)
    expired = now - timedelta(seconds=settings.GENERATION_EXPIRES_S) - margin
    visibility = settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"]
    abandoned = now - timedelta(seconds=visibility) - margin
    closed = [
        (
            GenerationRun.objects.filter(
                status=GenerationRun.Status.pending, created_at__lt=expired
            ),
            "nobody picked this run up in time — please start it again",
        ),
        (
            GenerationRun.objects.filter(
                status=GenerationRun.Status.running, updated_at__lt=abandoned
            ),
            "the worker stopped during this run — please start it again",
        ),
    ]
    count = 0
    users = set()
    for runs, error in closed:
        for run in runs.select_related("job_application"):
            updated = GenerationRun.objects.filter(pk=run.pk, status=run.status).update(
                status=GenerationRun.Status.failed,
                error=error,
                updated_at=timezone.now(),
            )
            if updated:
                count += 1
                users.add(run.job_application.user_id)
                publish_event(
                    run.pk,
                    {
                        "event": "failed",
                        "status": GenerationRun.Status.failed,
                        "error": error,
                    },
                )
    for user_id in users:
        notify_when_drained(user_id)
    if count:
        logger.warning("reap_stuck_runs: closed %s runs", count)
    return count
