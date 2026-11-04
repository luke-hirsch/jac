"""spa.signals — the session flag after MFA, a profile for every new user, and uploads leaving with
their rows (the privacy policy's deletion promise)."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from lukehirsch.storage import delete_on_commit, purge_user_uploads
from spa.models import PersonalityProfile, PortfolioBlock, UserProfile


def on_mfa_authenticator_used(sender, request, user, authenticator, **kwargs):
    """Mark the session as MFA-authenticated after any successful authenticator use."""
    request.session["mfa_authenticated"] = True
    request.session.modified = True


@receiver(post_save, sender=get_user_model())
def _create_profile_on_user_creation(sender, instance, created, **kwargs):
    if created:
        from spa.portfolio import mint_handle

        UserProfile.objects.create(user=instance, handle=mint_handle(instance.username))
        PersonalityProfile.objects.create(user=instance)


@receiver(post_delete, sender=get_user_model())
def _purge_uploads_on_account_deletion(sender, instance, **kwargs):
    # Capture the id now: Django clears instance.pk right after the post_delete signals.
    user_id = instance.pk
    transaction.on_commit(lambda: purge_user_uploads(user_id), robust=True)


@receiver(post_delete, sender=UserProfile)
def _delete_profile_files(sender, instance, **kwargs):
    delete_on_commit(instance.avatar, instance.signature)


@receiver(post_delete, sender=PortfolioBlock)
def _delete_block_image(sender, instance, **kwargs):
    delete_on_commit(instance.image)
