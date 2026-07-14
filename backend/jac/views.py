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

import logging
from datetime import date

from celery import current_app as celery_current_app
from django.db import transaction
from django.db.models.functions import Coalesce, Least
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from llm_connector import can_web_search
from llm_connector.conf import get_alias_strength
from lukehirsch.permissions import IsOwner, IsOwnerOrReadOnly
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from jac.cv import CV
from jac.llm_prompts import AddressSearch, LetterChat, ParagraphRewrite
from jac.models import (
    ApplicationLayout,
    Certification,
    Domain,
    Education,
    GenerationRun,
    Job,
    JobApplication,
    Language,
    Location,
    Project,
    ResumeSnippet,
    Skill,
    TransitionError,
)
from jac.serializers import (
    ApplicationLayoutSerializer,
    CertificationSerializer,
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
    ResumeSnippetSerializer,
    SkillSerializer,
)
from jac.tasks import GENERATION_EXPIRES_S, generate_run, publish_event

logger = logging.getLogger(__name__)


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
                count, _ = qs.delete()
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


class ResumeSnippetViewSet(BulkActionMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsOwner]
    serializer_class = ResumeSnippetSerializer
    search_fields = ["title", "content"]
    filterset_fields = ["kind", "is_active", "domains", "skills"]
    ordering_fields = ["kind", "title", "created_at", "updated_at"]

    def get_queryset(self):
        return ResumeSnippet.objects.filter(user=self.request.user).order_by(
            "kind", "title"
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


class JobApplicationViewSet(viewsets.ModelViewSet):
    """The user-facing applications. Create binds (or inline-creates) the posting; the
    tailored content (`cv_content`/`cover_letter`) stays editable via PATCH — that's also
    how the SPA "applies" a finished generation run."""

    serializer_class = JobApplicationSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    filterset_fields = ["status"]
    ordering_fields = ["created_at", "updated_at", "status"]

    def get_queryset(self):
        return (
            JobApplication.objects.filter(user=self.request.user)
            .select_related("posting", "layout")
            .prefetch_related("runs")
        )

    @extend_schema(
        request=inline_serializer(
            "ParagraphRewrite",
            {
                "text": serializers.CharField(),
                "instruction": serializers.CharField(required=False, allow_blank=True),
                "alias": serializers.CharField(required=False),
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
            alias=(request.data.get("alias") or "default").strip() or "default",
            user=request.user,
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
                "alias": serializers.CharField(required=False),
                "body": serializers.CharField(required=False, allow_blank=True),
                "messages": serializers.ListField(child=serializers.DictField()),
            },
        ),
        responses=OpenApiResponse(description="{'reply': str, 'revision': str | None}"),
    )
    @action(detail=True, methods=["post"])
    def chat(self, request, pk=None):
        """Ephemeral letter-refinement chat. The client sends its current draft body plus
        the transcript so far; only the model's reply (and an optional proposed revision)
        travels back — nothing is persisted. Standard+ aliases only: the UI filters, the
        strength probe here is the backstop. Sync like `rewrite` — one completion per
        turn."""
        application = self.get_object()
        body = request.data.get("body") or ""
        messages = request.data.get("messages") or []
        alias = (request.data.get("alias") or "default").strip() or "default"
        problem = self._chat_problem(body, messages)
        if problem:
            return Response(problem, status=status.HTTP_400_BAD_REQUEST)
        if get_alias_strength(alias, user=request.user) == "light":
            return Response(
                {
                    "alias": [
                        "This model is too small to chat usefully — pick a "
                        "standard or strong one."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        out = LetterChat(
            body,
            messages,
            posting_text=application.posting.posting_text,
            language=application.posting.language or "en",
            alias=alias,
            user=request.user,
        ).reply()
        if not out["reply"] and not out["revision"]:
            return Response(
                {"detail": "The language model returned nothing — try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(out)

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
            "FindAddress",
            {"alias": serializers.CharField(required=False)},
        ),
        responses=OpenApiResponse(
            description="{'address': {field: value}, 'sources': [url]}"
        ),
    )
    @action(detail=True, methods=["post"])
    def find_address(self, request, pk=None):
        """Web-search the employer's postal address for the letter's recipient block.
        Sync like `rewrite` (a single web-search call); nothing is persisted — the
        client merges the found fields into its letter_meta draft. 400 when the alias
        can't web-search (the UI only offers capable ones; this is the backstop),
        502 when the search yields nothing usable."""
        application = self.get_object()
        alias = (request.data.get("alias") or "default").strip() or "default"
        if not can_web_search(alias, request.user):
            return Response(
                {"alias": ["This model cannot web-search."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        posting = application.posting
        company = getattr(getattr(posting, "address", None), "company", "")
        result = AddressSearch(
            company, posting.posting_text, alias=alias, user=request.user
        ).search()
        if not result["ok"]:
            return Response(
                {
                    "detail": "No address found — try another model or fill it in by hand."
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"address": result["address"], "sources": result["sources"]})

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

    def get_serializer_class(self):
        if self.action == "create":
            return GenerationRunCreateSerializer
        return GenerationRunSerializer

    def perform_create(self, serializer):
        run = serializer.save()
        # `expires` keeps a task queued while no worker is up from firing hours later;
        # the task id is what cancel() revokes.
        async_result = generate_run.apply_async(
            args=[run.pk], expires=GENERATION_EXPIRES_S
        )
        run.task_id = async_result.id
        run.save(update_fields=["task_id"])
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
            except Exception:  # noqa: BLE001 — broker down must not block the cancel
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
