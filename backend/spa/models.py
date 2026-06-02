"""spa.models — user profile and (future) portfolio link models.

UserProfile is a one-to-one extension of auth.User created automatically
via post_save signal.  It holds identity, contact info, UI preferences, and
notification settings that don't belong on the auth.User model itself.
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _


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

    # Locale — timezone key drives Celery reminder scheduling (Phase 5)
    timezone = models.CharField(max_length=64, default="UTC")

    # UI preferences
    theme = models.CharField(max_length=6, choices=Theme, default=Theme.system)
    contrast = models.CharField(max_length=6, choices=Contrast, default=Contrast.normal)

    # Notifications
    email_reminders = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile({self.user})"


@receiver(post_save, sender=get_user_model())
def _create_profile_on_user_creation(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
