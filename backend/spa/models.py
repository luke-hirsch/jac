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

from spa.personality_questions import MAX_ANSWER_LEN


def _avatar_path(instance, filename):
    """Store one avatar per user, named by pk so uploads auto-replace."""
    return f"avatars/{instance.user_id}{Path(filename).suffix.lower()}"


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
    """Per-user personality questionnaire + a cached, LLM-distilled dossier.

    Answers are free text keyed by question id; the dossier is regenerated when answers change
    (dossier_stale). Used by the JAC cover-letter personal paragraph and (later) the portfolio.
    """

    user = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE, related_name="personality"
    )
    answers = models.JSONField(default=dict, blank=True)  # {question_id: text}
    dossier = models.TextField(blank=True)  # distilled, cached
    answers_updated_at = models.DateTimeField(null=True, blank=True)
    dossier_built_at = models.DateTimeField(null=True, blank=True)
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

    def ensure_dossier(self, *, alias: str = "default", user=None) -> str:
        """Return the dossier, distilling (1 LLM call) if missing or stale. '' if no answers."""
        if not self.has_answers():
            return ""
        if self.dossier and not self.dossier_stale():
            return self.dossier
        from spa.distill import PersonalityDistiller

        # Resolve the slug->prompt map from the DB so a user's own questions render as
        # their real wording in the distiller prompt, not a bare slug.
        labels = {
            q.slug: q.prompt for q in PersonalityQuestion.objects.for_user(self.user)
        }
        text = PersonalityDistiller(
            self.answers, labels=labels, alias=alias, user=user
        ).distill()
        if text:
            self.dossier = text
            self.dossier_built_at = timezone.now()
            self.save(update_fields=["dossier", "dossier_built_at", "updated_at"])
        return self.dossier or ""


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
