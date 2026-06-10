from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.db.models import OuterRef, Q, Subquery, UniqueConstraint
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


def _earliest_started_subquery(related_model):
    """Subquery returning the smallest non-null `started` date among rows of
    `related_model` (Job or Project) whose `skills` M2M includes the outer
    Skill row. Used by `SkillManager` below.
    """
    return Subquery(
        related_model.objects.filter(skills=OuterRef("pk"), started__isnull=False)
        .order_by("started")
        .values("started")[:1]
    )


def _min_ignoring_none(*values):
    """min() of the args, skipping None. Returns None if every arg is None."""
    valid = [v for v in values if v is not None]
    return min(valid) if valid else None


class SkillManager(models.Manager):
    """Default Skill manager. Annotates every queryset with the earliest
    related Job/Project start date so `Skill.years_of_experience` resolves
    without per-row aggregate queries — important for list endpoints, where
    serializing N skills would otherwise issue 2N extra queries.
    """

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(
                _earliest_job_started=_earliest_started_subquery(Job),
                _earliest_project_started=_earliest_started_subquery(Project),
            )
        )


class DomainManager(models.Manager):
    """Domain manager. `for_user(user)` returns the user's own domains plus
    the system defaults in a single query, so viewsets and the CV pipeline
    don't have to repeat the union.
    """

    def for_user(self, user):
        return self.filter(
            Q(user=user) | Q(user__username=settings.SYSTEM_USER_USERNAME)
        )

    def defaults(self):
        return self.filter(user__username=settings.SYSTEM_USER_USERNAME)


class Domain(models.Model):
    """Tagging taxonomy shared across all CvEntry types. Rows owned by the
    `SYSTEM_USER_USERNAME` user are read-only defaults visible to everyone;
    all other rows are user-owned and only visible to their owner.
    """

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="domains"
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    if TYPE_CHECKING:
        objects: DomainManager
    else:
        objects = DomainManager()

    class Meta:
        verbose_name = "Domain / Industry"
        verbose_name_plural = "Domains / Industries"
        constraints = [UniqueConstraint("user", "name", name="unique_domain_per_user")]

    def __str__(self):
        return self.name


class Location(models.Model):
    """Reusable geo-reference attached to jobs, projects, and education entries."""

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="locations"
    )
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=100, null=True, blank=True)
    street = models.CharField(max_length=100, null=True, blank=True)
    zip = models.CharField(max_length=20, null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)


class CvEntry(models.Model):
    """Abstract base for all user-scoped career entries.

    Every concrete entry carries a user FK, description, and audit timestamps.
    Subclasses add their own domain-specific fields.
    """

    class Meta:
        abstract = True

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="%(class)s_entries"
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    description = models.TextField(blank=True)


class Education(CvEntry):
    """Degree or formal study period."""

    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, null=True, blank=True
    )
    institution = models.CharField(max_length=200)
    field_of_study = models.CharField(max_length=200, blank=True)
    started = models.DateField()
    ended = models.DateField(null=True, blank=True)
    degree = models.CharField(max_length=100, null=True, blank=True)
    grade = models.CharField(max_length=50, null=True, blank=True)
    skills = models.ManyToManyField("Skill", blank=True)
    domains = models.ManyToManyField(Domain, blank=True)


class Certification(CvEntry):
    """Externally issued credential (certificate, licence, course completion)."""

    name = models.CharField(max_length=200)
    issuer = models.CharField(max_length=200)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    credential_id = models.CharField(max_length=200, blank=True)
    url = models.URLField(blank=True)
    skills = models.ManyToManyField("Skill", blank=True, related_name="certifications")
    domains = models.ManyToManyField(Domain, blank=True)


class Skill(CvEntry):
    """A single technical, soft, or domain skill with a self-assessed proficiency."""

    class Proficiency_Choices(models.TextChoices):
        beginner = "beginner", _("Beginner")
        intermediate = "intermediate", _("Intermediate")
        advanced = "advanced", _("Advanced")
        expert = "expert", _("Expert")

    class Category(models.TextChoices):
        technical = "technical", _("Technical")
        soft = "soft", _("Soft")
        domain = "domain", _("Domain / Industry")
        other = "other", _("Other")

    name = models.CharField(max_length=200)
    proficiency = models.CharField(
        max_length=12,
        choices=Proficiency_Choices,
        default=Proficiency_Choices.intermediate,
    )
    category = models.CharField(
        max_length=10,
        choices=Category,
        default=Category.technical,
    )
    domains = models.ManyToManyField(Domain, blank=True)
    first_used = models.DateField(null=True, blank=True)
    related_skills = models.ManyToManyField("self", blank=True)
    builds_on = models.ManyToManyField(
        "self",
        symmetrical=False,
        related_name="enables",
        blank=True,
    )
    years_of_experience_override = models.IntegerField(null=True, blank=True)
    certification = models.ForeignKey(
        Certification, on_delete=models.SET_NULL, null=True, blank=True
    )

    objects = SkillManager()

    @property
    def years_of_experience(self) -> int | None:
        """Whole years since the earliest evidence this skill has been used.

        Considers three sources, takes the smallest:
          - `first_used` on the Skill itself
          - the earliest `started` among Jobs that include this skill
          - the earliest `started` among Projects that include this skill

        is overridden by the `years_of_experience_override` field

        The two related-model dates are precomputed by `SkillManager`, so
        reading this property issues zero extra queries — *provided* the
        instance came from `Skill.objects.…`. A freshly-created Skill (one
        you just `.create()`'d in the same scope) won't carry the annotations
        yet; refetch via `Skill.objects.get(pk=skill.pk)` if you need them.
        """
        if self.years_of_experience_override is not None:
            return self.years_of_experience_override

        earliest = _min_ignoring_none(
            self.first_used,
            getattr(self, "_earliest_job_started", None),
            getattr(self, "_earliest_project_started", None),
        )
        if earliest is None:
            return None

        return (timezone.localdate() - earliest).days // 365


class Job(CvEntry):
    """Employment or contract position."""

    class job_type_choices(models.TextChoices):
        full_time = "ft", _("Full-time")
        part_time = "pt", _("Part-time")
        contract = "ct", _("Contract")
        freelance = "fl", _("Freelance")
        internship = "in", _("Internship")
        volunteer = "vl", _("Volunteer")

    title = models.CharField(max_length=200)
    company = models.CharField(max_length=200)
    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, null=True, blank=True
    )
    job_type = models.CharField(
        max_length=10, choices=job_type_choices, default=job_type_choices.full_time
    )
    skills = models.ManyToManyField(Skill, blank=True)
    domains = models.ManyToManyField(Domain, blank=True)
    started = models.DateField()
    ended = models.DateField(null=True, blank=True)
    url = models.URLField(blank=True)


class Project(CvEntry):
    """Personal or professional project, side project, or open-source contribution."""

    name = models.CharField(max_length=200)
    skills = models.ManyToManyField(Skill, blank=True)
    domains = models.ManyToManyField(Domain, blank=True)
    job = models.ForeignKey(
        Job,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, null=True, blank=True
    )
    started = models.DateField(null=True, blank=True)
    ended = models.DateField(null=True, blank=True)
    url = models.URLField(blank=True)


class Language(CvEntry):
    """A spoken or written language with self-assessed fluency."""

    class Fluency(models.TextChoices):
        native = "native", _("Native")
        fluent = "fluent", _("Fluent")
        professional = "professional", _("Professional working proficiency")
        conversational = "conversational", _("Conversational")
        basic = "basic", _("Basic")

    name = models.CharField(max_length=100)
    fluency = models.CharField(max_length=16, choices=Fluency, default=Fluency.basic)
    certification = models.ForeignKey(
        Certification, null=True, blank=True, on_delete=models.SET_NULL
    )


class ResumeSnippet(models.Model):
    class Kind(models.TextChoices):
        intro = "intro", _("Introduction")
        achievement = "achievement", _("Achievement")
        value_statement = "value_statement", _("Value statement")
        closing = "closing", _("Closing")
        other = "other", _("Other")

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="snippets"
    )
    title = models.CharField(max_length=200)
    content = models.TextField()
    kind = models.CharField(max_length=16, choices=Kind, default=Kind.other)
    domains = models.ManyToManyField(Domain, blank=True)
    skills = models.ManyToManyField(Skill, blank=True)
    job = models.ForeignKey(
        Job,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resume_snippets",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resume_snippets",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "title"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.title}"  # type: ignore[attr-defined]
