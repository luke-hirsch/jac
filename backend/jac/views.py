"""DRF viewsets for the JAC career models.

User-scoping pattern: every viewset that wraps a `CvEntry` subclass (plus
Location) overrides `get_queryset` to return only rows owned by
`self.request.user`. This is the single line that prevents user A from
reading, editing, or deleting user B's data through the API.

`DomainViewSet` is the odd one: reads include the system-default rows owned
by the sentinel user (so users can pick from the shared taxonomy), but writes
are restricted to the requesting user's own rows, which makes PUT/DELETE on a
default 404 naturally. `IsOwnerOrReadOnly` is layered on top as
defense-in-depth.

The `user` FK on writes is injected by the serializers via
`HiddenField(default=CurrentUserDefault())`, so we don't need to set it here.
"""

import json
import logging
import time
from dataclasses import replace
from datetime import date

from asgiref.sync import sync_to_async
from celery import current_app as celery_current_app
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models.functions import Coalesce, Least
from django.http import StreamingHttpResponse
from django.utils.text import slugify
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from kombu.exceptions import OperationalError
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings as drf_api_settings
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from jac.career_import import CareerNotEmpty, load_sample_career
from jac.cv import CV
from jac.llm_prompts import LetterChat
from jac.models import (
    ApplicationLayout,
    Certification,
    CvAttachment,
    Domain,
    Education,
    GenerationRun,
    Job,
    JobApplication,
    Language,
    Location,
    Mode,
    Project,
    Skill,
    TransitionError,
)
from jac.queues import (
    count_generations,
    generation_budget_left,
    lock_generation_budget,
    queue_for,
)
from jac.register import ADDRESS_FORMS
from jac.serializers import (
    ApplicationBatchSerializer,
    ApplicationLayoutSerializer,
    ApplicationTransitionSerializer,
    CertificationSerializer,
    CvAttachmentSerializer,
    CvSerializer,
    DomainSerializer,
    EducationSerializer,
    GenerationRunCreateSerializer,
    GenerationRunSerializer,
    JobApplicationSerializer,
    JobSerializer,
    LanguageSerializer,
    LocationSerializer,
    ProjectSerializer,
    RewriteRequestSerializer,
    ShortenRequestSerializer,
    SkillSerializer,
)
from jac.tasks import generate_run, notify_when_drained, publish_event
from llm_connector.base import LLMWarmingUp
from llm_connector.conf import ExecutorError, default_executor, resolve_executor
from llm_connector.jobs import start_ai_job
from llm_connector.serializers import AiJobSerializer
from lukehirsch.permissions import IsOwner, IsOwnerOrReadOnly
from lukehirsch.private_media import private_file_response
from lukehirsch.throttling import ChatDailyThrottle, UploadThrottle
from spa.portfolio import freeze_link, link_for_application
from spa.serializers import PortfolioLinkSerializer

logger = logging.getLogger(__name__)


def _enqueue_run(run: GenerationRun) -> None:
    """Queue the Celery task and remember its id (what cancel() revokes). A broker that can't take
    it fails the run with a message; the page still answers."""
    try:
        async_result = generate_run.apply_async(  # type: ignore[no-untyped-call]
            args=[run.pk],
            queue=queue_for(run.provider),
            expires=settings.GENERATION_EXPIRES_S,
        )
    except OperationalError:
        logger.exception("run %s: the broker refused the task", run.pk)
        run.status = GenerationRun.Status.failed
        run.error = "the job queue isn't reachable right now — try again in a moment"
        run.save(update_fields=["status", "error", "updated_at"])
        return
    run.task_id = async_result.id
    run.save(update_fields=["task_id"])


def _revoke_run_task(run_pk: int, task_id: str) -> None:
    """Stop a run's Celery task. Best effort: a broker hiccup must not fail the request."""
    try:
        celery_current_app.control.revoke(task_id, terminate=True)
    except Exception:
        logger.warning(
            "run %s: revoke of task %s failed", run_pk, task_id, exc_info=True
        )


def _letter_language(data, application) -> str:
    """The LETTER's language as the editor holds it — the user can switch the letter after
    the run — else the posting's. Capped like `ShortenRequestSerializer.language`."""
    language = data.get("language")
    language = language[:8] if isinstance(language, str) else ""
    return language or application.posting.language or "en"


def _address_form(data) -> str | None:
    """The letter's form of "you" as the editor sends it ('' = none), or None for anything
    else — the caller 400s, since the value picks a prompt clause."""
    form = data.get("address_form") or ""
    return form if form in ("", *ADDRESS_FORMS) else None


_UNKNOWN_FORM = {"address_form": [f"One of {', '.join(ADDRESS_FORMS)}, or blank."]}


async def _serve_chunk_by_chunk(iterator):
    """A sync generator as an ASGI stream. Given the generator itself, StreamingHttpResponse under
    ASGI consumes it with list() first (`__aiter__`), so the reply would arrive in one piece when
    it's complete. thread_sensitive keeps every step on the request's one DB thread."""
    finished = object()
    step = sync_to_async(next, thread_sensitive=True)
    try:
        while (chunk := await step(iterator, finished)) is not finished:
            yield chunk
    finally:
        await sync_to_async(iterator.close, thread_sensitive=True)()


class BulkActionMixin:
    @extend_schema(
        request=inline_serializer(
            "BulkAction",
            {
                "action": serializers.ChoiceField(["delete", "patch_domains"]),
                "ids": serializers.ListField(child=serializers.IntegerField()),
                "add": serializers.ListField(
                    child=serializers.IntegerField(), required=False
                ),
                "remove": serializers.ListField(
                    child=serializers.IntegerField(), required=False
                ),
            },
        ),
        responses=OpenApiResponse(description="{'deleted': n} or {'updated': n}"),
    )
    @action(detail=False, methods=["post"])
    def bulk(self, request):
        op = request.data.get("action")
        ids = request.data.get("ids", [])
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            return Response(
                {"ids": ["Expected a list of integer IDs"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = self.get_queryset().filter(pk__in=ids)  # type: ignore[]
        found = {obj.pk for obj in qs}
        missing = [i for i in ids if i not in found]
        if missing:
            return Response(
                {"ids": [f"Not found or not yours: {missing}"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if op == "delete":
            with transaction.atomic():
                _count, _ = qs.delete()  # type: ignore[unused-variable]
            return Response({"deleted": len(found)})

        if op == "patch_domains":
            model = self.get_queryset().model  # type: ignore[]
            if not hasattr(model, "domains"):
                return Response(
                    {"action": ["patch_domains not supported for this resource."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            allowed = set(
                Domain.objects.for_user(request.user).values_list("pk", flat=True)
            )
            add = request.data.get("add") or []
            remove = request.data.get("remove") or []
            for ids_to_patch in (add, remove):
                if not isinstance(ids_to_patch, list) or not all(
                    isinstance(i, int) for i in ids_to_patch
                ):
                    return Response(
                        {"domains": ["Expected a list of integer IDs"]},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            bad = [d for d in (*add, *remove) if d not in allowed]
            if bad:
                return Response(
                    {"domains": [f"Not found or not yours: {bad}"]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            with transaction.atomic():
                for obj in qs:
                    if add:
                        obj.domains.add(*add)
                    if remove:
                        obj.domains.remove(*remove)
            return Response({"updated": len(found)})

        return Response(
            {"action": ['Expected "delete" or "patch_domains".']},
            status=status.HTTP_400_BAD_REQUEST,
        )


class DomainViewSet(BulkActionMixin, viewsets.ModelViewSet):
    serializer_class = DomainSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrReadOnly]
    search_fields = ["name"]
    ordering_fields = ["name"]

    def get_queryset(self):
        if self.action in ("list", "retrieve"):
            return Domain.objects.for_user(self.request.user).order_by("name")
        return Domain.objects.filter(user=self.request.user).order_by("name")


class LocationViewSet(BulkActionMixin, viewsets.ModelViewSet):
    serializer_class = LocationSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["city"]
    filterset_fields = ["country"]
    ordering_fields = ["city", "country"]

    def get_queryset(self):
        return Location.objects.filter(user=self.request.user).order_by("city")


class EducationViewSet(BulkActionMixin, viewsets.ModelViewSet):
    serializer_class = EducationSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["institution", "degree", "field_of_study"]
    filterset_fields = ["domains"]
    ordering_fields = [
        "started",
        "ended",
        "institution",
        "field_of_study",
        "favourite",
        "created_at",
        "updated_at",
    ]

    def get_queryset(self):
        return Education.objects.filter(user=self.request.user).order_by("-started")


class CertificationViewSet(BulkActionMixin, viewsets.ModelViewSet):
    serializer_class = CertificationSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["name", "issuer"]
    filterset_fields = ["domains"]
    ordering_fields = [
        "issued_on",
        "expires_on",
        "name",
        "issuer",
        "favourite",
        "created_at",
        "updated_at",
    ]

    def get_queryset(self):
        return Certification.objects.filter(user=self.request.user).order_by(
            "-issued_on"
        )


class SkillViewSet(BulkActionMixin, viewsets.ModelViewSet):
    serializer_class = SkillSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["name"]
    filterset_fields = ["category", "proficiency", "domains"]
    ordering_fields = [
        "name",
        "first_used",
        "proficiency",
        "experience_since",
        "favourite",
        "created_at",
        "updated_at",
    ]

    def get_queryset(self):
        far_future = date(9999, 12, 31)
        return (
            Skill.objects.filter(user=self.request.user)
            .annotate(
                experience_since=Least(
                    Coalesce("first_used", far_future),
                    Coalesce("_earliest_job_started", far_future),
                    Coalesce("_earliest_project_started", far_future),
                )
            )
            .order_by("name")
        )


class JobViewSet(BulkActionMixin, viewsets.ModelViewSet):
    serializer_class = JobSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["title", "company"]
    filterset_fields = ["domains", "job_type"]
    ordering_fields = [
        "started",
        "ended",
        "title",
        "company",
        "favourite",
        "created_at",
        "updated_at",
    ]

    def get_queryset(self):
        return Job.objects.filter(user=self.request.user).order_by("-started")


class ProjectViewSet(BulkActionMixin, viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["name"]
    filterset_fields = ["domains"]
    ordering_fields = [
        "started",
        "ended",
        "name",
        "favourite",
        "created_at",
        "updated_at",
    ]

    def get_queryset(self):
        return Project.objects.filter(user=self.request.user).order_by(
            "-started", "name"
        )


class LanguageViewSet(BulkActionMixin, viewsets.ModelViewSet):
    serializer_class = LanguageSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["name"]
    filterset_fields = ["fluency"]
    ordering_fields = ["name", "fluency", "favourite", "created_at", "updated_at"]

    def get_queryset(self):
        return Language.objects.filter(user=self.request.user).order_by("name")


class CVEntryListView(APIView):
    """A read-only view of all CV entries for the requesting user, across all types."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=CvSerializer)
    def get(self, request):
        cv = CV(request.user.pk)
        context = {"request": request}
        return Response(
            {
                "skills": SkillSerializer(
                    cv.entries["skills"], many=True, context=context
                ).data,
                "jobs": JobSerializer(
                    cv.entries["jobs"], many=True, context=context
                ).data,
                "educations": EducationSerializer(
                    cv.entries["educations"], many=True, context=context
                ).data,
                "certifications": CertificationSerializer(
                    cv.entries["certifications"], many=True, context=context
                ).data,
                "projects": ProjectSerializer(
                    cv.entries["projects"], many=True, context=context
                ).data,
                "languages": LanguageSerializer(
                    cv.entries["languages"], many=True, context=context
                ).data,
            }
        )


class ApplicationLayoutViewSet(viewsets.ModelViewSet):
    """Render layouts: reads include the system defaults (same split as `DomainViewSet`),
    writes are restricted to the user's own rows."""

    serializer_class = ApplicationLayoutSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrReadOnly]
    throttle_classes = [UploadThrottle]
    search_fields = ["name"]
    ordering_fields = ["name"]

    def get_queryset(self):
        if self.action in ("list", "retrieve"):
            return ApplicationLayout.objects.for_user(self.request.user)
        return ApplicationLayout.objects.filter(user=self.request.user)


class _LiveScopedRateThrottle(ScopedRateThrottle):
    """`ScopedRateThrottle.THROTTLE_RATES` is bound to `api_settings.DEFAULT_
    THROTTLE_RATES` once, at import time of `rest_framework.throttling` — a later
    `override_settings(REST_FRAMEWORK=...)` (tests, or any future ops override)
    updates `api_settings` but never that stale class attribute. Read it live."""

    @property
    def THROTTLE_RATES(self):
        return drf_api_settings.DEFAULT_THROTTLE_RATES


class SampleCareerView(APIView):
    """Load the fictional sample career into the requesting user's EMPTY career DB: the two-minute
    path for someone trying jac on the spot. It refuses rather than merges, so it can never clobber
    anything, and it only writes rows: no LLM, no run."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [_LiveScopedRateThrottle]
    throttle_scope = "sample-career"

    @extend_schema(
        request=None,
        responses={
            201: OpenApiResponse(description="{'created': {section: rows created}}"),
            409: OpenApiResponse(description="The user already has career entries"),
        },
    )
    def post(self, request):
        try:
            created = load_sample_career(request.user)
        except CareerNotEmpty:
            return Response(
                {"detail": "You already have career entries, so the sample stays out."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({"created": created}, status=status.HTTP_201_CREATED)


class JobApplicationViewSet(viewsets.ModelViewSet):
    """The user-facing applications. Create binds (or inline-creates) the posting; the
    tailored content (`cv_content`/`cover_letter`) stays editable via PATCH — that's also
    how the SPA "applies" a finished generation run."""

    serializer_class = JobApplicationSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    filterset_fields = ["status"]
    ordering_fields = ["created_at", "updated_at", "status"]
    # Base attribute so @action(..., throttle_scope=...) on `chat` is a legal
    # per-action initkwarg (APIView.as_view requires hasattr(cls, key)); every
    # other action is untouched since ScopedRateThrottle isn't in DEFAULT_THROTTLE_CLASSES.
    throttle_scope = None

    def get_queryset(self):
        return (
            JobApplication.objects.filter(user=self.request.user)
            .select_related("posting", "layout")
            .prefetch_related("runs")
        )

    def perform_create(self, serializer):
        """Create the application and queue its first generation run."""
        application = serializer.save()
        executor = default_executor(self.request.user)
        # The check, the row and the count are one step, so parallel requests can't all take the
        # last slot.
        with transaction.atomic():
            lock_generation_budget(self.request.user)
            if generation_budget_left(self.request.user) <= 0:
                return  # over today's cap: the application stands, the run waits for a click
            run = GenerationRun.objects.create(
                job_application=application,
                mode=Mode.standard,
                provider=executor.provider,
                model=executor.model or "",
            )
            count_generations(self.request.user)
        _enqueue_run(run)

    def perform_destroy(self, instance):
        """The runs leave with the application, so their tasks have to stop too: a running one
        would go on computing for nobody."""
        active = list(
            instance.runs.filter(
                status__in=[GenerationRun.Status.pending, GenerationRun.Status.running]
            )
            .exclude(task_id="")
            .values_list("pk", "task_id")
        )
        instance.delete()
        for run_pk, task_id in active:
            _revoke_run_task(run_pk, task_id)

    @extend_schema(
        request=ApplicationBatchSerializer,
        responses=OpenApiResponse(description="The created applications"),
    )
    @action(detail=False, methods=["post"])
    def batch(self, request):
        """Queue several postings in one go: paste, leave, get one mail. Each one counts against
        the daily cap, and all of them fit or none is created."""
        ser = ApplicationBatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            executor = resolve_executor(
                request.user, data["provider"], data["model"]
            )
        except ExecutorError as exc:
            return Response(
                {"provider": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST
            )
        applications, runs = [], []
        # The rows first, the broker after: an open transaction holds the budget's row lock.
        with transaction.atomic():
            lock_generation_budget(request.user)
            left = generation_budget_left(request.user)
            if len(data["postings"]) > left:
                return Response(
                    {"detail": f"Today's limit leaves room for {left} more runs."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            for text in data["postings"]:
                created = JobApplicationSerializer(
                    data={"posting_text": text}, context=self.get_serializer_context()
                )
                created.is_valid(raise_exception=True)
                application = created.save()
                applications.append(application)
                runs.append(
                    GenerationRun.objects.create(
                        job_application=application,
                        mode=Mode.standard,
                        provider=executor.provider,
                        model=executor.model or "",
                    )
                )
            count_generations(request.user, len(runs))
        for run in runs:
            _enqueue_run(run)
        return Response(
            JobApplicationSerializer(
                applications, many=True, context=self.get_serializer_context()
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=None,
        responses=OpenApiResponse(
            description="The application's active portfolio link (slug, url, …)"
        ),
    )
    @action(detail=True, methods=["post"], url_path="portfolio-link")
    def portfolio_link(self, request, pk=None):
        """Get-or-create the application's public portfolio link — idempotent; the
        export card calls this when the portfolio-QR toggle first goes on (guide 4).
        Lazy creation keeps auto-runs that never export from littering link rows."""
        application = self.get_object()
        link = link_for_application(application)
        return Response(
            PortfolioLinkSerializer(link, context={"request": request}).data
        )

    @extend_schema(
        request=RewriteRequestSerializer,
        responses=OpenApiResponse(
            description="202: the AI job to poll; its result is {'text': rewritten passage}"
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        throttle_classes=[_LiveScopedRateThrottle],
        throttle_scope="ai-job",
    )
    def rewrite(self, request, pk=None):
        """Queue an AI rewrite of one passage of the letter body. Only the passage travels both
        ways — the client splices the result back into its draft; nothing is persisted on the
        application. A job that yields nothing fails, so the client keeps the original text."""
        application = self.get_object()
        serializer = RewriteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            resolve_executor(
                request.user, data.get("provider", ""), data.get("model", "")
            )
        except ExecutorError as exc:
            return Response(
                {"provider": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST
            )
        job = start_ai_job(
            "letter_rewrite",
            user=request.user,
            payload={
                "text": data["text"],
                "instruction": data.get("instruction", ""),
                "language": _letter_language(data, application),
                "address_form": data.get("address_form", ""),
                "provider": data.get("provider", ""),
                "model": data.get("model", ""),
            },
        )
        return Response(AiJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        request=ShortenRequestSerializer,
        responses=OpenApiResponse(
            description="202: the AI job to poll; its result is {'body': shortened letter body}"
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        throttle_classes=[_LiveScopedRateThrottle],
        throttle_scope="ai-job",
    )
    def shorten(self, request, pk=None):
        """Queue shortening the whole letter body to a word budget the CLIENT measured.

        The page fit lives in the browser (react-pdf is what actually paginates the
        letter), so the server never computes the budget — it arrives as `target_words`
        and this endpoint only does the language part. Like `/rewrite/`, nothing is
        persisted on the application: the client splices the result into its draft.

        Failure contract: 400 for a payload the model should never see; a job that yields
        nothing fails (the client's shorten loop then keeps the text it already had and
        stops).
        """
        application = self.get_object()
        # Validate before resolving an executor: a bad budget must never cost an LLM
        # lookup, and it must 400 on its own merits rather than on a missing config.
        serializer = ShortenRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            resolve_executor(
                request.user, data.get("provider", ""), data.get("model", "")
            )
        except ExecutorError as exc:
            return Response(
                {"provider": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST
            )
        job = start_ai_job(
            "letter_shorten",
            user=request.user,
            payload={
                "body": data["body"],
                "target_words": data["target_words"],
                "language": data.get("language") or application.posting.language or "en",
                "address_form": data.get("address_form", ""),
                "provider": data.get("provider", ""),
                "model": data.get("model", ""),
            },
        )
        return Response(AiJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        request=inline_serializer(
            "LetterChatRequest",
            {
                "body": serializers.CharField(required=False, allow_blank=True),
                "messages": serializers.ListField(child=serializers.DictField()),
                "language": serializers.CharField(required=False, allow_blank=True),
                "address_form": serializers.ChoiceField(
                    ADDRESS_FORMS, required=False, allow_blank=True
                ),
                "provider": serializers.CharField(required=False),
                "model": serializers.CharField(required=False),
            },
        ),
        responses=OpenApiResponse(
            description="text/event-stream: data: {delta|done|error}"
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        throttle_classes=[_LiveScopedRateThrottle, ChatDailyThrottle],
        throttle_scope="llm-chat",
    )
    def chat(self, request, pk=None):
        """Job-hunting assistant, streamed. The client sends its current draft body
        plus the transcript so far; the reply streams back as SSE deltas — nothing
        is persisted server-side. Any resolvable executor chats; two throttles (pace and
        daily budget) and one reply at a time bound abuse of a streaming LLM endpoint on an
        authed surface."""
        application = self.get_object()

        body = request.data.get("body") or ""
        messages = request.data.get("messages") or []
        try:
            executor = resolve_executor(
                request.user,
                request.data.get("provider", ""),
                request.data.get("model", ""),
            )
        except ExecutorError as exc:
            return Response(
                {"provider": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST
            )
        problem = self._chat_problem(body, messages)
        if problem:
            return Response(problem, status=status.HTTP_400_BAD_REQUEST)
        address_form = _address_form(request.data)
        if address_form is None:
            return Response(_UNKNOWN_FORM, status=status.HTTP_400_BAD_REQUEST)

        chat = LetterChat(
            body=body,
            transcript=messages,
            # Somebody is watching: a model that hangs between two chunks ends the reply here,
            # not at the pipeline's minutes-long timeout.
            executor=replace(executor, timeout=self.CHAT_WALL_CLOCK_S),
            posting_text=str(application.posting.posting_text),
            cv_content=application.cv_content,
            language=_letter_language(request.data, application),
            address_form=address_form,
        )
        # Call now, synchronously — `stream()` itself only ever builds a generator
        # (no LLM call happens yet), so this costs nothing, but it resolves
        # `chat.stream` as a bound method *now* rather than when `events()` first
        # iterates, which is well after the request has returned control to WSGI.
        deltas = chat.stream()

        # One reply at a time per account: each holds a web thread for up to CHAT_WALL_CLOCK_S, and
        # the pace alone allows dozens side by side. events() releases it; the TTL is for a stream
        # nobody reads, whose generator never starts and so never reaches its `finally`.
        lock = f"chat-stream:{request.user.pk}"
        if not cache.add(lock, 1, self.CHAT_WALL_CLOCK_S):
            return Response(
                {"messages": ["Your last reply is still being written — wait for it."]},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        def events():
            deadline = time.monotonic() + self.CHAT_WALL_CLOCK_S
            try:
                for delta in deltas:
                    if time.monotonic() > deadline:
                        yield (
                            'data: {"error": "The reply took too long — '
                            'try again."}\n\n'
                        )
                        return
                    yield f"data: {json.dumps({'delta': delta})}\n\n"
                yield 'data: {"done": true}\n\n'
            except LLMWarmingUp:
                yield (
                    'data: {"error": "The AI is warming up — '
                    'try again in a minute."}\n\n'
                )
            except Exception:
                # The exception text can name the model server's address: log it, don't send it.
                logger.exception("letter chat stream failed")
                yield (
                    'data: {"error": "The AI is unavailable right now — '
                    'try again later."}\n\n'
                )
            finally:
                cache.delete(lock)

        response = StreamingHttpResponse(
            _serve_chunk_by_chunk(events()), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # nginx: don't buffer the stream
        return response

    # A hung model must not pin an ASGI threadpool worker.
    CHAT_WALL_CLOCK_S = 120

    @staticmethod
    def _chat_problem(body, messages) -> dict | None:
        """First 400-worthy problem with a chat request, or None. Shape-checks the
        transcript (roles, non-empty content, user speaks last) and the size caps —
        all before any LLM cost."""
        if not isinstance(messages, list) or not messages:
            return {"messages": ["At least one message is required."]}
        for m in messages:
            if (
                not isinstance(m, dict)
                or m.get("role") not in ("user", "assistant")
                or not isinstance(m.get("content"), str)
                or not m["content"].strip()
            ):
                return {
                    "messages": [
                        "Each message needs a user/assistant role and text content."
                    ]
                }
        if messages[-1]["role"] != "user":
            return {"messages": ["The last message must be the user's."]}
        if sum(len(m["content"]) for m in messages) > LetterChat._MAX_TRANSCRIPT_CHARS:
            return {"messages": ["Transcript too long — start a fresh chat."]}
        if not isinstance(body, str):
            return {"body": ["Expected text."]}
        if len(body) > LetterChat._MAX_BODY_CHARS:
            return {"body": ["Letter body too long."]}
        return None

    @extend_schema(
        request=ApplicationTransitionSerializer,
        responses=JobApplicationSerializer,
    )
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        """Advance the application through its lifecycle. Legal moves live in
        `JobApplication.TRANSITIONS`; an unknown or illegal target is a 400. The model stamps
        the side-effect fields (approved_at / sent_at / responded_at, delivery_method,
        response_outcome, note) — `status` itself is read-only over plain PATCH."""
        application = self.get_object()
        serializer = ApplicationTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            application.apply_transition(**serializer.validated_data)
        except TransitionError as exc:
            return Response({"to": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        application.save()
        if application.status == JobApplication.StatusChoices.sent:
            freeze_link(
                application
            )  # snapshot the sent selection; no-op without a link
        return Response(
            JobApplicationSerializer(application, context={"request": request}).data
        )


class GenerationRunViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """Create + read async generation runs. Create attaches a pending run to one of the
    user's applications and enqueues the Celery task; the SPA then streams progress over
    the WebSocket. Retrieve is the snapshot used to rehydrate after a refresh."""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return GenerationRun.objects.filter(
            job_application__user=self.request.user
        ).select_related("job_application__posting")

    def get_throttles(self):
        if self.action == "create":
            self.throttle_scope = "generation"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_serializer_class(self):
        if self.action == "create":
            return GenerationRunCreateSerializer
        return GenerationRunSerializer

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():  # one step, see JobApplicationViewSet.perform_create
            lock_generation_budget(request.user)
            if generation_budget_left(request.user) <= 0:
                return Response(
                    {"detail": "You've reached today's generation limit."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            run = ser.save()
            count_generations(request.user)
        _enqueue_run(run)
        out = GenerationRunSerializer(run)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Abort a queued or running run: revoke the Celery task, mark the run failed,
        and publish the terminal event so open sockets update. Idempotent — cancelling
        a finished run returns it unchanged. The task side never resurrects a cancelled
        run (it only claims unfinished ones and only finishes `running` ones)."""
        run = self.get_object()
        if run.status in (GenerationRun.Status.done, GenerationRun.Status.failed):
            return Response(GenerationRunSerializer(run).data)
        if run.task_id:
            _revoke_run_task(run.pk, run.task_id)
        run.status = GenerationRun.Status.failed
        run.error = GenerationRun.CANCELLED_ERROR
        run.save(update_fields=["status", "error", "updated_at"])
        publish_event(
            run.pk, {"event": "failed", "status": run.status, "error": run.error}
        )
        # This may have been the last one in line: the runs before it can now be reported.
        notify_when_drained(request.user.pk)
        return Response(GenerationRunSerializer(run).data)


class CvAttachmentViewSet(viewsets.ModelViewSet):
    """The user's reusable attachment library. Owner-scoped; filterable by entry link
    (`?job=<pk>` / `?education=<pk>` / `?certification=<pk>`) so an entry form can list its
    own files. Multipart for the file upload."""

    serializer_class = CvAttachmentSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [UploadThrottle]
    filterset_fields = ["job", "education", "certification"]
    ordering_fields = ["created_at", "label"]

    def get_queryset(self):
        return CvAttachment.objects.filter(user=self.request.user).order_by("id")

    def perform_update(self, serializer):
        """The quota counts the rows' files, so a replaced file can't stay behind on disk."""
        old_name = serializer.instance.file.name
        attachment = serializer.save()
        if attachment.file.name != old_name:
            attachment.file.storage.delete(old_name)

    def perform_destroy(self, instance):
        """Likewise: upload + delete in a loop would otherwise fill the disk under the quota."""
        instance.delete()
        instance.file.delete(save=False)

    @extend_schema(responses={200: OpenApiResponse(description="The PDF (owner only)")})
    @action(detail=True, methods=["get"], url_path="file")
    def download(self, request, pk=None):
        """The PDF itself. Owner only: get_queryset scopes to request.user, so another user's
        id is a 404 — indistinguishable from a missing one."""
        attachment = self.get_object()
        name = slugify(attachment.label) or f"attachment-{attachment.pk}"
        return private_file_response(
            attachment.file, content_type="application/pdf", filename=f"{name}.pdf"
        )
