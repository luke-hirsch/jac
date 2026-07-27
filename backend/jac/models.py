from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import OuterRef, Subquery, UniqueConstraint
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from lukehirsch.managers import SystemScopedManager


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


class Mode(models.TextChoices):
    """How a generation selects/writes — the strategy axis; the run's executor
    (provider + model) is the machine axis. `standard` = instruct-labelled
    selection + polish-licence writer, every executor. `high` = holistic
    conversational selection + compose-licence writer (sees the posting, always
    audited) — commercial-only; the serializer rejects it on HirschAI. `manual`
    never enters the pipeline (the user hand-curates; CVFilter's manual branch
    keeps even a buggy caller at zero LLM/embed calls). THIS is the canonical
    list — nothing else hardcodes it."""

    manual = "manual", _("No AI")
    standard = "standard", _("Standard")
    high = "high", _("High")


def normalize_mode(value: str | None) -> str:
    """Coerce any input to a valid `Mode`. Blank/unknown/legacy -> `standard`
    (the AI default; the SPA offers `manual` itself when nothing is reachable)."""
    return str(value) if value in Mode.values else Mode.standard


class TransitionError(ValueError):
    """Raised when an illegal `JobApplication` status transition is attempted."""


class DomainManager(SystemScopedManager):
    """Domain manager — system-default scoping, see `SystemScopedManager`."""


class ApplicationLayout(models.Model):
    """Render layout for a `JobApplication` (page geometry, fonts, section order).

    `template` holds a declarative JSON spec consumed by the frontend react-pdf
    renderer. Rows owned by the system user are shared read-only defaults
    (same pattern as `Domain`); users add their own on top.
    """

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="layouts"
    )
    name = models.CharField(max_length=50)
    template = models.FileField(upload_to="application_layouts", blank=True)

    if TYPE_CHECKING:
        objects: SystemScopedManager
    else:
        objects = SystemScopedManager()

    class Meta:
        ordering = ["name"]
        constraints = [UniqueConstraint("user", "name", name="unique_layout_per_user")]

    def __str__(self):
        return self.name


def default_application_layout():
    """Pk of the system "default" layout, creating the sentinel user + row if missing.

    Doubles as the `JobApplication.layout` field default and the SET_DEFAULT target
    when a user deletes one of their own layouts.
    """
    from django.contrib.auth.models import User

    system, _ = User.objects.get_or_create(
        username=settings.SYSTEM_USER_USERNAME, defaults={"is_active": False}
    )
    layout, _ = ApplicationLayout.objects.get_or_create(user=system, name="default")
    return layout.pk


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

    # Max entries of this concrete type a user may flag as `favourite`. Favourites get a
    # small ranking nudge in the CV pipeline (see `CVFilter._FAVOURITE_BONUS` in cv.py); the
    # cap keeps that influence deliberate. Override per subclass; None = unlimited.
    FAVOURITE_LIMIT: int | None = None

    class Meta:
        abstract = True

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="%(class)s_entries"
    )
    favourite = models.BooleanField(default=False)
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

    @classmethod
    def favourite_count(cls, user, exclude_pk=None) -> int:
        """How many of `user`'s entries of this type are flagged favourite.

        `exclude_pk` drops the row being edited so an update doesn't count itself.
        """
        qs = cls._default_manager.filter(user=user, favourite=True)
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        return qs.count()

    def clean(self):
        """Enforce the per-type favourite cap. The API enforces the same rule via
        `FavouriteLimitMixin` (DRF doesn't call `full_clean`); this keeps admin/forms honest.
        """
        super().clean()
        if (
            self.favourite
            and self.FAVOURITE_LIMIT is not None
            and self.user.id is not None
            and self.favourite_count(self.user.id, exclude_pk=self.pk)
            >= self.FAVOURITE_LIMIT
        ):
            raise ValidationError(
                {
                    "favourite": (
                        f"You can mark at most {self.FAVOURITE_LIMIT} "
                        f"{self._meta.verbose_name_plural} as favourite."
                    )
                }
            )


class Education(CvEntry):
    """Degree or formal study period."""

    FAVOURITE_LIMIT = 2

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

    FAVOURITE_LIMIT = 3

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

    FAVOURITE_LIMIT = 10

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

    FAVOURITE_LIMIT = 4

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

    FAVOURITE_LIMIT = 3

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

    FAVOURITE_LIMIT = 3

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


class JobPosting(models.Model):
    """A job posting the user is tailoring an application to.

    Owns the raw posting text plus the role title and detected language. Parent for the
    extracted recipient address (`JobPostAddress`, 1:1) and — later — the generated CV and
    cover letter (roadmap items #2/#3). User-scoped like every other JAC record.
    """

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="job_postings"
    )
    title = models.CharField(max_length=200, blank=True)
    posting_text = models.TextField()
    # ISO-639-1 code of the posting language (e.g. "en", "de"); drives salutation/subject and
    # the weave-prompt language hint. Best-effort from AddressExtract; defaults to English.
    language = models.CharField(max_length=8, default="en")
    source_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Soft-delete: postings are never hard-deleted (applications hang off them);
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title or 'Posting'} ({self.created_at:%Y-%m-%d})"


class JobPostAddress(models.Model):
    """Employer contact block extracted from a job posting.

    Every field is `blank=True` — extraction is lossy and a posting rarely states all of them;
    the cover-letter renderer simply omits empty lines. `email`/`phone` are captured for later
    application-sending / tracking.
    """

    job_posting = models.OneToOneField(
        JobPosting, on_delete=models.CASCADE, related_name="address"
    )
    company = models.CharField(max_length=200, blank=True)
    contact_name = models.CharField(max_length=200, blank=True)
    street = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    zip = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)

    def __str__(self) -> str:
        return self.company or f"Address for posting {self.job_posting.pk}"


class JobApplication(models.Model):
    """The user-facing application for a posting.

    Holds the (editable) tailored CV JSON + cover-letter text and the chosen render layout.
    Generation runs attach to it (`runs` reverse); a finished run auto-fills
    `cv_content`/`cover_letter` only while both are still empty — after that the user applies
    a run's result explicitly (a PATCH from the SPA), so re-runs never clobber edits.
    """

    class StatusChoices(models.TextChoices):
        draft = "draft", _("Draft")
        approved = "approved", _("Approved")
        sent = "sent", _("Sent")
        follow_up_sent = "follow_up", _("Follow-up sent")
        response = "response", _("Response from Posting")
        inactive = "inactive", _("Inactive")

    class DeliveryMethod(models.TextChoices):
        email = "email", _("Email")
        manual = "manual", _("Manual / download")

    class ResponseOutcome(models.TextChoices):
        interview = "interview", _("Interview")
        offer = "offer", _("Offer")
        rejected = "rejected", _("Rejected")
        other = "other", _("Other")

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="job_applications"
    )
    posting = models.ForeignKey(
        JobPosting, on_delete=models.CASCADE, related_name="applications"
    )
    cv_content = models.JSONField(default=dict, blank=True)
    cover_letter = models.TextField(blank=True)
    letter_meta = models.JSONField(default=dict, blank=True)
    pinned_entries = models.JSONField(default=list, blank=True)
    layout = models.ForeignKey(
        ApplicationLayout,
        on_delete=models.SET_DEFAULT,
        default=default_application_layout,
        related_name="applications",
    )
    status = models.CharField(
        max_length=12, choices=StatusChoices.choices, default=StatusChoices.draft
    )
    deadline = models.DateField(null=True, blank=True)
    delivery_method = models.CharField(
        max_length=8, choices=DeliveryMethod.choices, blank=True
    )
    response_outcome = models.CharField(
        max_length=12, choices=ResponseOutcome.choices, blank=True
    )
    notes = models.TextField(blank=True)
    sent_by_system = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Application for {self.posting.title or 'posting'} ({self.status})"

    def clean(self) -> None:
        if (
            self.posting_id is not None  # type: ignore[attr-defined]
            and not self.posting.active
        ):
            self.status = self.StatusChoices.inactive
        return super().clean()

    TRANSITIONS: dict[str, set[str]] = {
        StatusChoices.draft: {StatusChoices.approved, StatusChoices.inactive},
        StatusChoices.approved: {
            StatusChoices.draft,
            StatusChoices.sent,
            StatusChoices.inactive,
        },
        StatusChoices.sent: {
            StatusChoices.follow_up_sent,
            StatusChoices.response,
            StatusChoices.inactive,
        },
        StatusChoices.follow_up_sent: {
            StatusChoices.response,
            StatusChoices.inactive,
        },
        StatusChoices.response: {StatusChoices.inactive},
        StatusChoices.inactive: {StatusChoices.draft},
    }

    def can_transition(self, to: str) -> bool:
        return to in self.TRANSITIONS.get(self.status, set())

    def apply_transition(
        self,
        to: str,
        *,
        delivery_method: str = "",
        response_outcome: str = "",
        note: str = "",
    ) -> None:
        """Validate + perform a status move, stamping the relevant side-effect fields.

        Raises `TransitionError` for an unknown target, an illegal move, or an unknown
        delivery-method / outcome value. Does **not** save — the caller persists, so a rejected
        move leaves both the instance and the DB row untouched.
        """
        if to not in self.StatusChoices.values:
            raise TransitionError(f"Unknown status {to!r}.")
        if not self.can_transition(to):
            raise TransitionError(
                f"Cannot move an application from {self.status!r} to {to!r}."
            )
        now = timezone.now()
        if to == self.StatusChoices.approved:
            self.approved_at = now
        elif to == self.StatusChoices.sent:
            if delivery_method:
                if delivery_method not in self.DeliveryMethod.values:
                    raise TransitionError(
                        f"Unknown delivery method {delivery_method!r}."
                    )
                self.delivery_method = delivery_method
            self.sent_at = now
        elif to == self.StatusChoices.response:
            if response_outcome:
                if response_outcome not in self.ResponseOutcome.values:
                    raise TransitionError(
                        f"Unknown response outcome {response_outcome!r}."
                    )
                self.response_outcome = response_outcome
            if note:
                self.notes = note
            self.responded_at = now
        self.status = to


class GenerationRun(models.Model):
    """One async CV + cover-letter generation. The view persists it `pending` and enqueues the
    Celery task (`jac.tasks.generate_run`), which streams progress over the `gen_<pk>` channel
    group and writes the final `result`. The SPA subscribes by WebSocket; a REST GET rehydrates
    the snapshot on refresh.
    """

    class Status(models.TextChoices):
        pending = "pending", _("Pending")
        running = "running", _("Running")
        done = "done", _("Done")
        failed = "failed", _("Failed")

    job_application = models.ForeignKey(
        JobApplication, on_delete=models.CASCADE, related_name="runs"
    )
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.standard)
    provider = models.CharField(max_length=32, default="ollama")
    model = models.CharField(max_length=100, blank=True)
    params = models.JSONField(default=dict, blank=True)

    # letter parameters overwriting from profile
    letter_tone = models.CharField(max_length=16, blank=True)
    letter_focus = models.CharField(max_length=16, blank=True)

    # CV scoping (all optional; map onto CV.__init__).
    domains = models.JSONField(default=list, blank=True)  # list[str] domain names
    started = models.DateField(null=True, blank=True)
    ended = models.DateField(null=True, blank=True)
    min_skill_proficiency = models.CharField(max_length=12, blank=True)

    # Lifecycle.
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.pending
    )
    task_id = models.CharField(
        max_length=64, blank=True
    )  # Celery task id — lets cancel() revoke the queued/running task
    stage = models.CharField(
        max_length=80, blank=True
    )  # last human-readable progress label
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]  # typing ClassVar

    def __str__(self) -> str:
        return f"GenerationRun {self.pk} ({self.status})"

    # Ownership and posting are derived through the application — single source of truth.
    @property
    def user(self):
        return self.job_application.user

    @property
    def posting(self) -> JobPosting:
        return self.job_application.posting


class ApplicationAttachment(models.Model):
    """A PDF appended to the exported application (cert, transcript, reference letter). Merged in
    `position` order client-side (pdf-lib) at export time. Validated as a PDF on upload."""

    application = models.ForeignKey(
        JobApplication, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to="application_attachments")
    label = models.CharField(max_length=120, blank=True)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return self.label or f"attachment {self.pk}"
