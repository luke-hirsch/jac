"""URL routing for the JAC app.

All viewsets are wired through DRF's `DefaultRouter`. Explicit `basename` is
required because every viewset overrides `get_queryset` instead of declaring
a class-level `queryset`, so the router can't infer it.

Mounted by the project at the prefix defined in `lukehirsch/urls.py`.
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from jac.views import (
    CVEntryListView,
    CertificationViewSet,
    DomainViewSet,
    EducationViewSet,
    JobViewSet,
    LanguageViewSet,
    LocationViewSet,
    ProjectViewSet,
    SkillViewSet,
)

router = DefaultRouter()
router.register("domains", DomainViewSet, basename="domain")
router.register("locations", LocationViewSet, basename="location")
router.register("education", EducationViewSet, basename="education")
router.register("certifications", CertificationViewSet, basename="certification")
router.register("skills", SkillViewSet, basename="skill")
router.register("jobs", JobViewSet, basename="job")
router.register("projects", ProjectViewSet, basename="project")
router.register("languages", LanguageViewSet, basename="language")

urlpatterns = router.urls + [
    path("cv/entries/", CVEntryListView.as_view(), name="cv-entries"),
]
