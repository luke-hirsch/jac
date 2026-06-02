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

    def get_queryset(self):
        if self.action in ("list", "retrieve"):
            return Domain.objects.for_user(self.request.user)
        return Domain.objects.filter(user=self.request.user)


class LocationViewSet(viewsets.ModelViewSet):
    serializer_class = LocationSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Location.objects.filter(user=self.request.user)


class EducationViewSet(viewsets.ModelViewSet):
    serializer_class = EducationSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Education.objects.filter(user=self.request.user)


class CertificationViewSet(viewsets.ModelViewSet):
    serializer_class = CertificationSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Certification.objects.filter(user=self.request.user)


class SkillViewSet(viewsets.ModelViewSet):
    serializer_class = SkillSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Skill.objects.filter(user=self.request.user)


class JobViewSet(viewsets.ModelViewSet):
    serializer_class = JobSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Job.objects.filter(user=self.request.user)


class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Project.objects.filter(user=self.request.user)


class LanguageViewSet(viewsets.ModelViewSet):
    serializer_class = LanguageSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Language.objects.filter(user=self.request.user)
