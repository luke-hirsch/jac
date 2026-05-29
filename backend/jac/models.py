from django.db import models
from django.db.models import Min
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Domain(models.Model):
    """Tagging taxonomy shared across all CvEntry types."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Location(models.Model):
    """Reusable geo-reference attached to jobs, projects, and education entries."""

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


class Certification(CvEntry):
    """Externally issued credential (certificate, licence, course completion)."""

    name = models.CharField(max_length=200)
    issuer = models.CharField(max_length=200)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    credential_id = models.CharField(max_length=200, blank=True)
    url = models.URLField(blank=True)


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
    certification = models.ForeignKey(
        Certification, on_delete=models.SET_NULL, null=True, blank=True
    )

    @property
    def years_of_experience(self) -> int | None:
        """Earliest evidence of use across first_used, linked jobs, and linked projects.

        Returns None when no dated evidence exists; otherwise full years elapsed
        since that earliest date.
        """
        candidates = [self.first_used]
        candidates.append(
            Job.objects.filter(skills=self).aggregate(m=Min("started"))["m"]
        )
        candidates.append(
            Project.objects.filter(skills=self).aggregate(m=Min("started"))["m"]
        )
        valid = [d for d in candidates if d is not None]
        if not valid:
            return None
        return (timezone.localdate() - min(valid)).days // 365


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
