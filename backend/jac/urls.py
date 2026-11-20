"""URL routing for the JAC app.

All viewsets are wired through DRF's `DefaultRouter`. Explicit `basename` is
required because every viewset overrides `get_queryset` instead of declaring
a class-level `queryset`, so the router can't infer it.

Mounted by the project at the prefix defined in `lukehirsch/urls.py`.
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from jac.views import (
    AccessTokenViewSet,
    ApplicationLayoutViewSet,
    CareerExportView,
    CareerImportView,
    CertificationViewSet,
    CvAttachmentViewSet,
    CVEntryListView,
    DomainViewSet,
    EducationViewSet,
    GenerationRunViewSet,
    JobApplicationViewSet,
    JobViewSet,
    LanguageViewSet,
    LocationViewSet,
    ProjectViewSet,
    SampleCareerView,
    SkillApplicationDetailView,
    SkillApplicationListView,
    SkillBundleView,
    SkillCareerChangeListView,
    SkillCareerChangeUndoView,
    SkillCareerView,
    SkillReviewView,
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
router.register("generations", GenerationRunViewSet, basename="generation")
router.register("applications", JobApplicationViewSet, basename="application")
router.register("layouts", ApplicationLayoutViewSet, basename="layout")
router.register("attachments", CvAttachmentViewSet, basename="attachment")
router.register("skill/tokens", AccessTokenViewSet, basename="skill-token")

urlpatterns = router.urls + [
    path("cv/entries/", CVEntryListView.as_view(), name="cv-entries"),
    path("sample-career/", SampleCareerView.as_view(), name="sample-career"),
    path("career/export/", CareerExportView.as_view(), name="career-export"),
    path("career/import/", CareerImportView.as_view(), name="career-import"),
    # The Claude skill: a personal token or the session (jac/skill.py).
    path("skill/career/", SkillCareerView.as_view(), name="skill-career"),
    path(
        "skill/applications/",
        SkillApplicationListView.as_view(),
        name="skill-applications",
    ),
    path(
        "skill/applications/<int:pk>/",
        SkillApplicationDetailView.as_view(),
        name="skill-application",
    ),
    path("skill/review/", SkillReviewView.as_view(), name="skill-review"),
    path(
        "skill/career/changes/",
        SkillCareerChangeListView.as_view(),
        name="skill-career-changes",
    ),
    path(
        "skill/career/changes/<int:pk>/undo/",
        SkillCareerChangeUndoView.as_view(),
        name="skill-career-change-undo",
    ),
    path("skill/bundle/", SkillBundleView.as_view(), name="skill-bundle"),
]
