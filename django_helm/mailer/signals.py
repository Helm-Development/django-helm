"""Receivers that record email-address lifecycle events for Helm to poll.

Helm polls ``UserEmailEvent`` rows to learn which verified accounts appeared
or changed address. Deleting a user removes that user's events, so a deleted
account leaves no trail. That also makes an account-merge flow come out
right: the duplicate's rename to a placeholder address disappears with it.

The work is split across ``pre_save`` and ``post_save`` so that only a save
that reached the database records anything. ``pre_save`` reads the stored
row and stashes it on the instance; ``post_save`` compares and writes the
event. A save that raises in between leaves no event, and a save whose
``update_fields`` touches neither ``email`` nor ``is_active`` costs no query.
Fixture loads (``raw``) are never recorded.
"""
from django.utils import timezone

from .models import UserEmailEvent

PRIOR_ATTR = '_helm_mailer_prior'
TRACKED_FIELDS = frozenset({'email', 'is_active'})


def normalize(email):
    return (email or '').strip().lower()


def record_prior_state(sender, instance, raw=False, using=None, update_fields=None, **kwargs):
    """pre_save: stash the stored ``email``/``is_active`` of an existing user."""
    instance.__dict__[PRIOR_ATTR] = None
    if raw or instance._state.adding or instance.pk is None:
        return
    if update_fields is not None and not TRACKED_FIELDS & set(update_fields):
        return
    instance.__dict__[PRIOR_ATTR] = (
        sender._default_manager.db_manager(using)
        .filter(pk=instance.pk)
        .values('email', 'is_active')
        .first()
    )


def _after(instance, prior, field, update_fields):
    """The value the row holds after this save."""
    if update_fields is None or field in update_fields:
        return getattr(instance, field)
    return prior[field]


def record_user_change(sender, instance, created=False, raw=False, using=None,
                       update_fields=None, **kwargs):
    """post_save: record an activation or an address change on an existing user."""
    prior = instance.__dict__.pop(PRIOR_ATTR, None)
    if created or raw or prior is None:
        return

    was_active = bool(prior['is_active'])
    is_active = bool(_after(instance, prior, 'is_active', update_fields))
    email = _after(instance, prior, 'email', update_fields) or ''

    if is_active and not was_active:
        kind, previous_email = UserEmailEvent.KIND_ACTIVATED, ''
    elif was_active and is_active and normalize(prior['email']) != normalize(email):
        kind, previous_email = UserEmailEvent.KIND_EMAIL_CHANGED, prior['email'] or ''
    else:
        return

    UserEmailEvent.objects.using(using).create(
        kind=kind,
        email=email,
        previous_email=previous_email,
        first_name=getattr(instance, 'first_name', '') or '',
        last_name=getattr(instance, 'last_name', '') or '',
        occurred_at=timezone.now(),
    )


def forget_deleted_user(sender, instance, using=None, **kwargs):
    """post_delete: drop events carrying the deleted user's current address."""
    email = getattr(instance, 'email', '')
    if not email:
        return
    UserEmailEvent.objects.using(using).filter(email__iexact=email).delete()
