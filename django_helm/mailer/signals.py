"""Receivers that record email-address lifecycle events for Helm to poll.

Helm polls ``UserEmailEvent`` rows to learn which verified accounts appeared
or changed address. Deleting a user removes that user's events, so a deleted
account leaves no trail. That also makes an account-merge flow come out
right: the duplicate's rename to a placeholder address disappears with it.
"""
from django.utils import timezone

from .models import UserEmailEvent


def normalize(email):
    return (email or '').strip().lower()


def _address(email):
    # Case is kept on purpose: a case-only edit is still a change worth recording.
    return (email or '').strip()


def record_user_change(sender, instance, **kwargs):
    """pre_save: record an activation or an address change on an existing user."""
    if instance._state.adding or instance.pk is None:
        return
    previous = sender._default_manager.filter(pk=instance.pk).values('email', 'is_active').first()
    if previous is None:
        return

    was_active = bool(previous['is_active'])
    is_active = bool(instance.is_active)

    if is_active and not was_active:
        kind, previous_email = UserEmailEvent.KIND_ACTIVATED, ''
    elif (
        was_active
        and is_active
        and _address(previous['email']) != _address(instance.email)
    ):
        kind, previous_email = UserEmailEvent.KIND_EMAIL_CHANGED, previous['email'] or ''
    else:
        return

    UserEmailEvent.objects.create(
        kind=kind,
        email=instance.email or '',
        previous_email=previous_email,
        first_name=instance.first_name or '',
        last_name=instance.last_name or '',
        occurred_at=timezone.now(),
    )


def forget_deleted_user(sender, instance, **kwargs):
    """post_delete: drop events carrying the deleted user's current address."""
    if not instance.email:
        return
    UserEmailEvent.objects.filter(email__iexact=instance.email).delete()
