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

from lukehirsch.permissions import IsOwner, IsOwnerOrReadOnly
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from jac.cv import CV
from jac.models import (
    Certification,
    Domain,
    Education,
    Job,
    Language,
    Location,
    Project,
    Skill,
)
from jac.serializers import (
    CertificationSerializer,
    DomainSerializer,
    EducationSerializer,
    JobSerializer,
    LanguageSerializer,
    LocationSerializer,
    ProjectSerializer,
    SkillSerializer,
)


class DomainViewSet(viewsets.ModelViewSet):
    serializer_class = DomainSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrReadOnly]
    search_fields = ["name"]

    def get_queryset(self):
        if self.action in ("list", "retrieve"):
            return Domain.objects.for_user(self.request.user).order_by("name")
        return Domain.objects.filter(user=self.request.user).order_by("name")


class LocationViewSet(viewsets.ModelViewSet):
    serializer_class = LocationSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["city"]
    filterset_fields = ["country"]

    def get_queryset(self):
        return Location.objects.filter(user=self.request.user).order_by("city")


class EducationViewSet(viewsets.ModelViewSet):
    serializer_class = EducationSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["institution", "degree", "field_of_study"]

    def get_queryset(self):
        return Education.objects.filter(user=self.request.user).order_by("-started")


class CertificationViewSet(viewsets.ModelViewSet):
    serializer_class = CertificationSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["name", "issuer"]

    def get_queryset(self):
        return Certification.objects.filter(user=self.request.user).order_by("-issued_on")


class SkillViewSet(viewsets.ModelViewSet):
    serializer_class = SkillSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["name"]
    filterset_fields = ["category", "proficiency", "domains"]

    def get_queryset(self):
        return Skill.objects.filter(user=self.request.user).order_by("name")


class JobViewSet(viewsets.ModelViewSet):
    serializer_class = JobSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["title", "company"]
    filterset_fields = ["domains", "job_type"]

    def get_queryset(self):
        return Job.objects.filter(user=self.request.user).order_by("-started")


class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["name"]
    filterset_fields = ["domains"]

    def get_queryset(self):
        return Project.objects.filter(user=self.request.user).order_by("-started", "name")


class LanguageViewSet(viewsets.ModelViewSet):
    serializer_class = LanguageSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["name"]
    filterset_fields = ["fluency"]

    def get_queryset(self):
        return Language.objects.filter(user=self.request.user).order_by("name")


class CVEntryListView(APIView):
    """A read-only view of all CV entries for the requesting user, across all types."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        cv = CV(request.user.pk)
        context = {"request": request}
        return Response(
            {
                "skills": SkillSerializer(cv.entries["skills"], many=True, context=context).data,
                "jobs": JobSerializer(cv.entries["jobs"], many=True, context=context).data,
                "educations": EducationSerializer(cv.entries["educations"], many=True, context=context).data,
                "certifications": CertificationSerializer(cv.entries["certifications"], many=True, context=context).data,
                "projects": ProjectSerializer(cv.entries["projects"], many=True, context=context).data,
                "languages": LanguageSerializer(cv.entries["languages"], many=True, context=context).data,
            }
        )
