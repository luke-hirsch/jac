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
from functools import partial

from asgiref.sync import sync_to_async
from celery import current_app as celery_current_app
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
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

from jac import mcp
from jac.application_transfer import (
    ApplicationImportError,
    ApplicationsNotEmpty,
    CareerMissing,
    export_applications,
    load_into_empty_applications,
)
from jac.career_changes import (
    ChangeError,
    StaleError,
    UndoRefused,
    batch_payload,
    entry_labels,
    undo_batch,
)
from jac.career_export import export_career
from jac.career_import import (
    CareerImportError,
    CareerNotEmpty,
    load_into_empty_career,
    load_sample_career,
)
from jac.cv import CV
from jac.llm_prompts import LetterChat
from jac.models import (
    AccessToken,
    ApplicationLayout,
    CareerChange,
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
    AccessTokenSerializer,
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
from jac.skill import (
    skill_bundle,
)
from jac.style import style_rules
from jac.tasks import generate_run, notify_when_drained, publish_event
from jac.tokens import (
    AccessTokenAuthentication,
    PathTokenAuthentication,
    mint_token,
)
from llm_connector.base import LLMWarmingUp
from llm_connector.conf import ExecutorError, default_executor, resolve_executor
from llm_connector.jobs import start_ai_job
from llm_connector.models import TransferConsent
from llm_connector.serializers import AiJobSerializer
from llm_connector.views import ConsentView
from lukehirsch.permissions import IsOwner, IsOwnerOrReadOnly
from lukehirsch.private_media import private_file_response
from lukehirsch.throttling import ChatDailyThrottle, UploadThrottle
from spa.models import PersonalityProfile
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
    """`?ordering=` also takes `years_of_experience` (most first with `-`): the years are counted
    in Python (`Skill.years_of_experience`), so that order is sorted in Python too, over the
    user's whole filtered list, before the page is cut. A user has dozens of skills, not
    thousands."""

    serializer_class = SkillSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["name"]
    filterset_fields = ["category", "proficiency", "domains"]
    ordering_fields = [
        "name",
        "first_used",
        "proficiency",
        "favourite",
        "created_at",
        "updated_at",
    ]
    YEARS = "years_of_experience"

    def get_queryset(self):
        return Skill.objects.filter(user=self.request.user).order_by("name")

    def list(self, request, *args, **kwargs):
        terms = [
            t.strip()
            for t in request.query_params.get("ordering", "").split(",")
            if t.strip()
        ]
        if not any(t.lstrip("-") == self.YEARS for t in terms):
            return super().list(request, *args, **kwargs)
        rows = sorted(
            self.filter_queryset(self.get_queryset()), key=lambda s: s.name.lower()
        )
        # Stable sorts, last term first, give the same order as one multi-key ORDER BY.
        for term in reversed(terms):
            field = term.lstrip("-")
            if field == self.YEARS:
                key = _years_key
            elif field in self.ordering_fields:
                key = partial(_field_key, field=field)
            else:
                continue
            rows.sort(key=key, reverse=term.startswith("-"))
        page = self.paginate_queryset(rows)
        if page is None:
            return Response(self.get_serializer(rows, many=True).data)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)


def _years_key(skill) -> int:
    """No years sorts as the least experience, as an unset date used to."""
    years = skill.years_of_experience
    return -1 if years is None else years


def _field_key(obj, field):
    value = getattr(obj, field)
    return (value is None, value)


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


# An exported career or application list is well under this; the nginx in front allows 12 MB.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class UploadError(ValueError):
    """The upload isn't a JSON file. The message is meant for the user."""


def _uploaded_json(request):
    """The JSON file in the upload's `file` field, parsed. It's a multipart upload, not a JSON
    body, because Django refuses a body over 2.5 MB (DATA_UPLOAD_MAX_MEMORY_SIZE) and a list of
    applications with their postings can be that."""
    upload = request.FILES.get("file")
    if upload is None:
        raise UploadError("No file: send it as `file`.")
    if upload.size > MAX_UPLOAD_BYTES:
        raise UploadError("The file is larger than 10 MB.")
    try:
        return json.loads(upload.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UploadError(
            "That file isn't JSON: pick the .json file jac wrote."
        ) from exc


class CareerExportView(APIView):
    """GET: the requesting user's career as the JSON `cv_import` reads (jac/career_export.py),
    for the CV overview's download. Only their own rows; it writes nothing."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={
            200: OpenApiResponse(
                description="{section: [entry, …]}, the cv_import shape"
            )
        }
    )
    def get(self, request):
        return Response(export_career(request.user))


class CareerImportView(APIView):
    """POST a career file (the download's shape) as `file`: it fills an EMPTY career, every row
    checked as the editor checks it. Like the sample, it refuses rather than merges."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]
    throttle_classes = [_LiveScopedRateThrottle]
    throttle_scope = "career-import"

    @extend_schema(
        request=inline_serializer(
            "CareerImportUpload", fields={"file": serializers.FileField()}
        ),
        responses={
            201: OpenApiResponse(
                description="{'created': {section: rows}, 'warnings': []}"
            ),
            400: OpenApiResponse(
                description="Not a career file, or a row breaks a rule"
            ),
            409: OpenApiResponse(description="The user already has career entries"),
        },
    )
    def post(self, request):
        try:
            created = load_into_empty_career(request.user, _uploaded_json(request))
        except CareerNotEmpty:
            return Response(
                {
                    "detail": "Your career already has entries: an import only fills an "
                    "empty one."
                },
                status=status.HTTP_409_CONFLICT,
            )
        except (UploadError, CareerImportError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"created": created, "warnings": []}, status=status.HTTP_201_CREATED
        )


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

    def get_throttles(self):
        if self.action == "create":
            self.throttle_scope = "application-create"
            return [_LiveScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        return (
            JobApplication.objects.filter(user=self.request.user)
            .select_related("posting", "layout")
            .prefetch_related("runs")
        )

    @extend_schema(
        responses={
            200: OpenApiResponse(
                description="{postings, applications}, CV picks by natural key "
                "(jac/application_transfer.py)"
            )
        }
    )
    @action(detail=False, methods=["get"], url_path="export")
    def export(self, request):
        """Every application with its posting, as the file the import takes back."""
        return Response(export_applications(request.user))

    @extend_schema(
        request=inline_serializer(
            "ApplicationsImportUpload", fields={"file": serializers.FileField()}
        ),
        responses={
            201: OpenApiResponse(
                description="{'created': {postings, applications}, 'warnings': [...]}"
            ),
            400: OpenApiResponse(
                description="Not an applications file, or a broken row"
            ),
            409: OpenApiResponse(
                description="The user has applications already, or no career yet"
            ),
        },
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="import",
        parser_classes=[MultiPartParser],
        throttle_classes=[_LiveScopedRateThrottle],
        throttle_scope="application-import",
    )
    def import_file(self, request):
        """POST an applications file as `file`: it fills an account without applications,
        CV picks matched against the career by natural key. It queues no run."""
        try:
            result = load_into_empty_applications(
                request.user, _uploaded_json(request), request=request
            )
        except ApplicationsNotEmpty:
            return Response(
                {
                    "detail": "You have applications already: an import only fills an "
                    "empty list."
                },
                status=status.HTTP_409_CONFLICT,
            )
        except CareerMissing:
            return Response(
                {
                    "detail": "Your career is empty: import it first, the applications' "
                    "CVs point at its entries."
                },
                status=status.HTTP_409_CONFLICT,
            )
        except (UploadError, ApplicationImportError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)

    def perform_create(self, serializer):
        """Create the application and queue its first generation run."""
        application = serializer.save()
        try:
            executor = default_executor(self.request.user)
        except ExecutorError as exc:
            # The default key has no model to run on. The run says so instead of running on
            # another executor; it never reached the queue, so it costs no budget.
            GenerationRun.objects.create(
                job_application=application,
                mode=Mode.standard,
                provider=exc.provider,
                status=GenerationRun.Status.failed,
                error=str(exc),
            )
            return
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
            executor = resolve_executor(request.user, data["provider"], data["model"])
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
                "language": data.get("language")
                or application.posting.language
                or "en",
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
            style_rules=style_rules(
                PersonalityProfile.objects.filter(user=request.user).first()
            ),
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


# --- the Claude skill ---------------------------------------------------------------------------


class _CareerChangeView(APIView):
    """The career edits Claude made over the connector, as the SPA shows them (Account → LLM,
    under Connectors): on the session only. Claude lists and undoes them with the connector's own
    tools."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [_LiveScopedRateThrottle]
    throttle_scope = "skill"


class CareerChangeListView(_CareerChangeView):
    """GET the latest batches of career edits (`jac.career_changes`), newest first."""

    LIMIT = 20

    @extend_schema(responses=OpenApiResponse(description="[batch]"))
    def get(self, request):
        rows = CareerChange.objects.filter(user=request.user)[: self.LIMIT]
        return Response([batch_payload(b) for b in rows])


class CareerChangeUndoView(_CareerChangeView):
    """POST: put a batch's entries back as they were. Someone else's batch is a 404."""

    @extend_schema(
        request=None,
        responses={
            200: OpenApiResponse(description="The batch, undone"),
            409: OpenApiResponse(description="{detail, stale?}: can't undo"),
        },
    )
    def post(self, request, pk):
        get_object_or_404(CareerChange, pk=pk, user=request.user)
        try:
            return Response(undo_batch(request, pk))
        except UndoRefused as err:
            return Response({"detail": str(err)}, status=status.HTTP_409_CONFLICT)
        except StaleError as err:
            return Response(
                {
                    "detail": err.detail,
                    "stale": err.ids,
                    "labels": entry_labels(request.user, err.ids),
                },
                status=status.HTTP_409_CONFLICT,
            )
        except ChangeError as err:
            return Response(
                {
                    "detail": "jac can't put these back as they were.",
                    "changes": err.errors,
                },
                status=status.HTTP_409_CONFLICT,
            )


@extend_schema(exclude=True)
class SkillMcpView(APIView):
    """The jac connector (`jac.mcp`): MCP over Streamable HTTP, stateless, one POST per message,
    plain JSON back. A personal token, in the Authorization header or as the URL's last segment;
    never the session (Anthropic's servers call it, not a browser)."""

    authentication_classes = [AccessTokenAuthentication, PathTokenAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [_LiveScopedRateThrottle]
    throttle_scope = "skill"

    def post(self, request, key=None):
        status_code, body = mcp.respond(request, request.body)
        if body is None:
            return HttpResponse(status=status.HTTP_202_ACCEPTED)
        return JsonResponse(
            body,
            status=status_code,
            safe=False,
            json_dumps_params={"ensure_ascii": False},
        )


class ClaudeConsentView(ConsentView):
    """The kept consent for Claude's connector tokens (Account → LLM, under Connectors).
    Withdrawing revokes every token: the connector stops, and the next token asks again."""

    scope = TransferConsent.Scope.claude

    def covered(self, user):
        return AccessToken.objects.filter(user=user)


class AccessTokenViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """The user's connector tokens: list, mint (the key is in this one response only), revoke.
    Session only: a token can't mint more of itself."""

    serializer_class = AccessTokenSerializer
    permission_classes = [IsAuthenticated]
    throttle_scope = None

    def get_queryset(self):
        return AccessToken.objects.filter(user=self.request.user)

    def get_throttles(self):
        if self.action == "create":
            self.throttle_scope = "skill-token"
            return [_LiveScopedRateThrottle()]
        return super().get_throttles()

    @extend_schema(
        responses={201: OpenApiResponse(description="The token row + its `key`, once")}
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if self.get_queryset().count() >= AccessToken.MAX_PER_USER:
            return Response(
                {
                    "detail": f"At most {AccessToken.MAX_PER_USER} tokens: revoke one first."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        token, key = mint_token(request.user, serializer.validated_data.get("name", ""))
        return Response(
            {**AccessTokenSerializer(token).data, "key": key},
            status=status.HTTP_201_CREATED,
        )


class SkillBundleView(APIView):
    """GET: the skill as a zip, ready to upload to Claude: one file that points at the connector,
    no token, no career and no rules (`jac.skill.skill_bundle`), so downloading it mints
    nothing."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OpenApiResponse(description="jac-skill.zip")})
    def get(self, request):
        response = HttpResponse(skill_bundle(), content_type="application/zip")
        response["Content-Disposition"] = 'attachment; filename="jac-skill.zip"'
        return response
