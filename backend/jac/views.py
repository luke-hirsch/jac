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
from datetime import date

from celery import current_app as celery_current_app
from django.db import transaction
from django.db.models.functions import Coalesce, Least
from django.http import StreamingHttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings as drf_api_settings
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from jac.cv import CV
from jac.llm_prompts import LetterChat, ParagraphRewrite
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
from jac.serializers import (
    ApplicationLayoutSerializer,
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
    SkillSerializer,
)
from jac.tasks import GENERATION_EXPIRES_S, generate_run, publish_event
from llm_connector.conf import ExecutorError, default_executor, resolve_executor
from lukehirsch.permissions import IsOwner, IsOwnerOrReadOnly
from spa.portfolio import freeze_link, link_for_application
from spa.serializers import PortfolioLinkSerializer

logger = logging.getLogger(__name__)


def _enqueue_run(run: GenerationRun) -> None:
    """Queue the Celery task and remember its id (what cancel() revokes)."""
    async_result = generate_run.apply_async(args=[run.pk], expires=GENERATION_EXPIRES_S)  # type: ignore[no-untyped-call]
    run.task_id = async_result.id
    run.save(update_fields=["task_id"])


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
        """creates application and evokes auto applicatiopn generation run after creation"""
        application = serializer.save()

        executor = default_executor(self.request.user)
        if executor is None:
            return
        run = GenerationRun.objects.create(
            job_application=application,
            mode=Mode.standard,
            provider=executor.provider,
            model=executor.model or "",
        )
        _enqueue_run(run)

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
        request=inline_serializer(
            "ParagraphRewrite",
            {
                "text": serializers.CharField(),
                "instruction": serializers.CharField(required=False, allow_blank=True),
                "provider": serializers.CharField(required=False),
                "model": serializers.CharField(required=False),
            },
        ),
        responses=OpenApiResponse(description="{'text': rewritten passage}"),
    )
    @action(detail=True, methods=["post"])
    def rewrite(self, request, pk=None):
        """AI-rewrite one passage of the letter body. Only the passage travels both ways —
        the client splices the result back into its draft; nothing is persisted here.
        Synchronous LLM call (one paragraph; see the guide's decision note). 502 when the
        model yields nothing, so the client keeps the original text."""
        application = self.get_object()
        text = request.data.get("text") or ""
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
        if not text.strip():
            return Response(
                {"text": ["This field is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(text) > ParagraphRewrite._MAX_CHARS:
            return Response(
                {"text": ["Passage too long — select a paragraph, not the letter."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rewritten = ParagraphRewrite(
            text,
            instruction=(request.data.get("instruction") or "").strip(),
            language=application.posting.language or "en",
            executor=executor,
        ).rewrite()
        if not rewritten:
            return Response(
                {"detail": "The language model returned nothing — try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"text": rewritten})

    @extend_schema(
        request=inline_serializer(
            "LetterChatRequest",
            {
                "body": serializers.CharField(required=False, allow_blank=True),
                "messages": serializers.ListField(child=serializers.DictField()),
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
        throttle_classes=[_LiveScopedRateThrottle],
        throttle_scope="llm-chat",
    )
    def chat(self, request, pk=None):
        """Job-hunting assistant, streamed. The client sends its current draft body
        plus the transcript so far; the reply streams back as SSE deltas — nothing
        is persisted server-side. Any resolvable executor chats; a scoped throttle
        (`llm-chat`) bounds abuse of a streaming LLM endpoint on an authed surface."""
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

        chat = LetterChat(
            body=body,
            transcript=messages,
            executor=executor,
            posting_text=str(application.posting.posting_text),
            cv_content=application.cv_content,
            language=str(application.posting.language),
        )
        # Call now, synchronously — `stream()` itself only ever builds a generator
        # (no LLM call happens yet), so this costs nothing, but it resolves
        # `chat.stream` as a bound method *now* rather than when `events()` first
        # iterates, which is well after the request has returned control to WSGI.
        deltas = chat.stream()

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
            except Exception as exc:
                logger.exception("letter chat stream failed")
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        response = StreamingHttpResponse(events(), content_type="text/event-stream")
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
        if len(body) > LetterChat._MAX_BODY_CHARS:
            return {"body": ["Letter body too long."]}
        return None

    @extend_schema(
        request=inline_serializer(
            "ApplicationTransition",
            {
                "to": serializers.ChoiceField(JobApplication.StatusChoices.values),
                "delivery_method": serializers.CharField(
                    required=False, allow_blank=True
                ),
                "response_outcome": serializers.CharField(
                    required=False, allow_blank=True
                ),
                "note": serializers.CharField(required=False, allow_blank=True),
            },
        ),
        responses=JobApplicationSerializer,
    )
    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        """Advance the application through its lifecycle. Legal moves live in
        `JobApplication.TRANSITIONS`; an unknown or illegal target is a 400. The model stamps
        the side-effect fields (approved_at / sent_at / responded_at, delivery_method,
        response_outcome, note) — `status` itself is read-only over plain PATCH."""
        application = self.get_object()
        try:
            application.apply_transition(
                request.data.get("to"),
                delivery_method=(request.data.get("delivery_method") or "").strip(),
                response_outcome=(request.data.get("response_outcome") or "").strip(),
                note=(request.data.get("note") or "").strip(),
            )
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

    def perform_create(self, serializer):
        run = serializer.save()
        _enqueue_run(run)
        self._created = run

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        self.perform_create(ser)
        out = GenerationRunSerializer(self._created)
        return Response(out.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Abort a queued or running run: revoke the Celery task, mark the run failed,
        and publish the terminal event so open sockets update. Idempotent — cancelling
        a finished run returns it unchanged. The task side never resurrects a cancelled
        run (it only claims `pending` and only finishes `running` ones)."""
        run = self.get_object()
        if run.status in (GenerationRun.Status.done, GenerationRun.Status.failed):
            return Response(GenerationRunSerializer(run).data)
        if run.task_id:
            try:
                celery_current_app.control.revoke(run.task_id, terminate=True)
            except Exception:
                logger.warning(
                    "cancel run %s: revoke of task %s failed",
                    run.pk,
                    run.task_id,
                    exc_info=True,
                )
        run.status = GenerationRun.Status.failed
        run.error = "cancelled by user"
        run.save(update_fields=["status", "error", "updated_at"])
        publish_event(
            run.pk, {"event": "failed", "status": run.status, "error": run.error}
        )
        return Response(GenerationRunSerializer(run).data)


class CvAttachmentViewSet(viewsets.ModelViewSet):
    """The user's reusable attachment library. Owner-scoped; filterable by entry link
    (`?job=<pk>` / `?education=<pk>` / `?certification=<pk>`) so an entry form can list its
    own files. Multipart for the file upload."""

    serializer_class = CvAttachmentSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    filterset_fields = ["job", "education", "certification"]
    ordering_fields = ["created_at", "label"]

    def get_queryset(self):
        return CvAttachment.objects.filter(user=self.request.user).order_by("id")
