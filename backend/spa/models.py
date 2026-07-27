"""spa.models — user profile and (future) portfolio link models.

UserProfile is a one-to-one extension of auth.User created automatically
via post_save signal.  It holds identity, contact info, UI preferences, and
notification settings that don't belong on the auth.User model itself.
"""

from pathlib import Path

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from lukehirsch.managers import SystemScopedManager
from django.core.exceptions import ValidationError
from django.db.models import Q
from spa.distill import StyleDistiller
from spa.personality_questions import MAX_ANSWER_LEN


def _avatar_path(instance, filename):
    """Store one avatar per user, named by pk so uploads auto-replace."""
    return f"avatars/{instance.user_id}{Path(filename).suffix.lower()}"


def _block_image_path(instance, filename):
    """Namespace block images per owner; Django suffixes duplicates itself."""
    return f"portfolio/blocks/{instance.user_id}/{Path(filename).name}"


class UserProfile(models.Model):
    """One-to-one extension of auth.User.

    Created automatically when a new User is saved.  Covers identity,
    professional contact links, locale, UI theme/accessibility preferences,
    and notification opt-ins.
    """

    class Theme(models.TextChoices):
        system = "system", _("Follow system")
        light = "light", _("Light")
        dark = "dark", _("Dark")

    class Contrast(models.TextChoices):
        normal = "normal", _("Normal")
        high = "high", _("High contrast")

    user = models.OneToOneField(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="profile",
    )

    # Identity
    display_name = models.CharField(max_length=100, blank=True)
    avatar = models.ImageField(upload_to=_avatar_path, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    signature = models.ImageField(upload_to="signatures", blank=True)

    # Professional contact — also used to pre-fill job applications in JAC
    phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)
    linkedin_url = models.URLField(blank=True)
    github_url = models.URLField(blank=True)

    show_socials = models.BooleanField(default=False)

    # Postal address — the sender block on JAC cover letters.
    street = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    zip = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)

    # Locale — timezone key drives Celery reminder scheduling (Phase 5)
    timezone = models.CharField(max_length=64, default="CET")

    # UI preferences
    theme = models.CharField(max_length=6, choices=Theme, default=Theme.system)
    contrast = models.CharField(max_length=6, choices=Contrast, default=Contrast.normal)

    # Notifications
    email_reminders = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile({self.user})"


class PersonalityProfile(models.Model):
    """Per-user personality questionnaire + cached LLM-distilled dossier, the letter tone×focus
    default, and a cached writing-style dossier distilled from a pasted sample. All three feed the
    JAC cover-letter writer (and, later, the portfolio)."""

    class Tone(models.TextChoices):
        personal = "personal", _("Personal")
        neutral = "neutral", _("Neutral")
        formal = "formal", _("Formal")

    class Focus(models.TextChoices):
        soft_skill = "soft_skill", _("Soft-skill focus")
        balanced = "balanced", _("Balanced")
        technical = "technical", _("Technical focus")

    user = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE, related_name="personality"
    )
    answers = models.JSONField(default=dict, blank=True)  # {question_id: text}
    dossier = models.TextField(blank=True)  # distilled, cached
    answers_updated_at = models.DateTimeField(null=True, blank=True)
    dossier_built_at = models.DateTimeField(null=True, blank=True)

    # letter paramters
    letter_tone = models.CharField(
        max_length=16, choices=Tone.choices, default=Tone.neutral
    )
    letter_focus = models.CharField(
        max_length=16, choices=Focus.choices, default=Focus.balanced
    )
    # writing style probe
    writing_sample = models.TextField(blank=True)
    style_dossier = models.TextField(blank=True)  # ai destill
    sample_updated_at = models.DateTimeField(null=True, blank=True)
    style_built_at = models.DateTimeField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Personality({self.user})"

    def has_answers(self) -> bool:
        return any((self.answers or {}).values())

    def dossier_stale(self) -> bool:
        if self.dossier_built_at is None:
            return True
        return bool(
            self.answers_updated_at and self.answers_updated_at > self.dossier_built_at
        )

    def ensure_dossier(self, executor) -> str:
        """Return the dossier, distilling (1 LLM call) if missing or stale. '' if no answers."""
        if not self.has_answers():
            return ""
        if self.dossier and not self.dossier_stale():
            return self.dossier
        from spa.distill import PersonalityDistiller

        labels = {
            q.slug: q.prompt for q in PersonalityQuestion.objects.for_user(self.user)
        }
        text = PersonalityDistiller(
            self.answers, labels=labels, executor=executor
        ).distill()
        if text:
            self.dossier = text
            self.dossier_built_at = timezone.now()
            self.save(update_fields=["dossier", "dossier_built_at", "updated_at"])
        return self.dossier or ""

    # --- writing-style cache (mirror of the dossier cache above) --------------------------

    def has_sample(self) -> bool:
        return bool((self.writing_sample or "").strip())

    def style_stale(self) -> bool:
        if self.style_built_at is None:
            return True
        return bool(
            self.sample_updated_at and self.sample_updated_at > self.style_built_at
        )

    def ensure_style_dossier(self, executor) -> str:
        """Return the style dossier, distilling (1 LLM call) if missing or stale. '' if no sample."""
        if not self.has_sample():
            return ""
        if self.style_dossier and not self.style_stale():
            return self.style_dossier

        text = StyleDistiller(self.writing_sample, executor=executor).distill()
        if text:
            self.style_dossier = text
            self.style_built_at = timezone.now()
            self.save(update_fields=["style_dossier", "style_built_at", "updated_at"])
        return self.style_dossier or ""


class PersonalityQuestion(models.Model):
    """A questionnaire prompt. Rows owned by the ``settings.SYSTEM_USER_USERNAME`` user are
    the shared defaults every user answers (seeded by ``seed_system_defaults``); a user's own
    rows are private questions they add on top — same read-only-defaults pattern as
    ``jac.Domain``. ``slug`` is the stable key the answer is stored under in
    ``PersonalityProfile.answers``, so editing a prompt never orphans its answer.
    """

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="personality_questions"
    )
    slug = models.SlugField(max_length=50)
    prompt = models.CharField(max_length=MAX_ANSWER_LEN)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SystemScopedManager()

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                "user", "slug", name="unique_question_slug_per_user"
            )
        ]

    def __str__(self):
        return self.prompt


class PortfolioBlock(models.Model):
    """Owner-authored portfolio content the career DB can't hold: a markdown text
    group or a captioned image. Domain-taggable so every filtering axis that applies
    to career entries applies to blocks too; `favourite` = featured-by-default (same
    axis as `CvEntry.favourite`, no cap — blocks are already hand-curated). Public id
    grammar: `block:<pk>` beside the career ids (`job:12`).
    """

    class Kind(models.TextChoices):
        text = "text", _("Text")
        image = "image", _("Image")

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="portfolio_blocks"
    )
    kind = models.CharField(max_length=5, choices=Kind, default=Kind.text)
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)  # markdown for text blocks; caption for images
    image = models.ImageField(upload_to=_block_image_path, blank=True)
    alt_text = models.CharField(max_length=200, blank=True)
    domains = models.ManyToManyField(
        "jac.Domain", blank=True, related_name="portfolio_blocks"
    )
    favourite = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title or f"{self.kind} block {self.pk}"

    def clean(self):
        """A block must carry its kind's payload. DRF doesn't call full_clean — the
        serializer repeats this rule; this keeps admin/forms honest (the CvEntry pattern,
        jac/models.py:200)."""
        super().clean()
        if self.kind == self.Kind.image and not self.image:
            raise ValidationError({"image": "An image block needs an image."})
        if self.kind == self.Kind.text and not (self.body or "").strip():
            raise ValidationError({"body": "A text block needs a body."})


class PortfolioLink(models.Model):
    """A personalised public portfolio URL (`/portfolio/<slug>`).

    `manual` links are owner-named and hand-curated; `application` links are minted by
    jac via `spa.portfolio.link_for_application` (readable-plus-entropy slug) and render
    the application's tailored selection — live while draft, frozen at the `sent`
    transition (`spa.portfolio.freeze_link`). Revocation is a timestamp, not a delete:
    the public queryset filters `revoked_at__isnull=True` (revoked ≡ never-existed →
    identical 404s) and the partial unique constraint below allows one *active* link per
    application while keeping revoked history for regenerate.
    """

    class Kind(models.TextChoices):
        manual = "manual", _("Manual")
        application = "application", _("Application")

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="portfolio_links"
    )
    slug = models.SlugField(max_length=80, unique=True)
    kind = models.CharField(max_length=12, choices=Kind, default=Kind.manual)
    title = models.CharField(max_length=200, blank=True)
    intro = models.TextField(blank=True)
    application = models.ForeignKey(
        "jac.JobApplication",
        on_delete=models.SET_NULL,  # a frozen page survives application deletion
        null=True,
        blank=True,
        related_name="portfolio_links",
    )
    # {"featured": ["job:12", "block:7", …], "domains": [names], "hide_explore": bool}
    # — ids only, joined against the live career DB at render time (deleted rows drop
    # silently, the cv-doc philosophy). Application links keep featured empty until the
    # sent-freeze; the public view falls back to the live cv_content meanwhile.
    content = models.JSONField(default=dict, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["application"],
                condition=Q(revoked_at__isnull=True, application__isnull=False),
                name="one_active_link_per_application",
            )
        ]

    def __str__(self):
        state = ", revoked" if self.revoked_at else ""
        return f"/{self.slug} ({self.kind}{state})"

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def revoke(self) -> None:
        """Idempotent soft-kill: the public path 404s from the next request on."""
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at", "updated_at"])


class PortfolioVisit(models.Model):
    """Daily visit bucket per link — deliberately GDPR-light (no IP/UA/timestamps) and
    bounded at links × days. Bumped by the public resolve view (guide 2), which skips
    the owner's own previews. Native-flow traffic is deliberately untracked (bots)."""

    link = models.ForeignKey(
        PortfolioLink, on_delete=models.CASCADE, related_name="visits"
    )
    day = models.DateField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["link", "day"], name="one_visit_row_per_link_day"
            )
        ]

    def __str__(self):
        return f"{self.link.slug} {self.day}: {self.count}"
