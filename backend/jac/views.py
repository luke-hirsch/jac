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

from datetime import date

from django.db import transaction
from django.db.models.functions import Coalesce, Least
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from lukehirsch.permissions import IsOwner, IsOwnerOrReadOnly
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from jac.cv import CV
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
from jac.tasks import generate_run


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
        generate_run.delay(run.pk)
        self._created = run

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        self.perform_create(ser)
        out = GenerationRunSerializer(self._created)
        return Response(out.data, status=status.HTTP_201_CREATED)
