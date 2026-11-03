"""Account counts served at /helm/user_accounts.

Defaults rely only on fields Django's ``AbstractUser`` guarantees. A customer
whose user model differs supplies ``HELM_ANALYTICS['user_accounts_counter']``,
a dotted path to a callable ``(since) -> dict`` whose values override defaults.
"""
from datetime import datetime, timezone

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Count, Q
from django.utils.module_loading import import_string

from .exclusions import excluded_user_hashes

FIELDS = (
    'total_users', 'active_users', 'inactive_users', 'activated_users',
    'not_activated_users', 'created_since', 'logged_in_since',
    'activated_since', 'staff_users', 'analytics_excluded_users',
)


def parse_since(value):
    """Parse an ISO 8601 string; naive values are taken as UTC."""
    message = 'since must be an ISO 8601 timestamp'
    if not isinstance(value, str):
        raise ValueError(message)
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(message) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def default_counts(since):
    agg = get_user_model()._default_manager.aggregate(
        total=Count('pk'),
        active=Count('pk', filter=Q(is_active=True)),
        activated=Count('pk', filter=Q(last_login__isnull=False)),
        created_since=Count('pk', filter=Q(date_joined__gte=since)),
        logged_in_since=Count('pk', filter=Q(last_login__gte=since)),
        staff=Count('pk', filter=Q(is_staff=True)),
    )
    return {
        'total_users': agg['total'],
        'active_users': agg['active'],
        'inactive_users': agg['total'] - agg['active'],
        'activated_users': agg['activated'],
        'not_activated_users': agg['total'] - agg['activated'],
        'created_since': agg['created_since'],
        'logged_in_since': agg['logged_in_since'],
        'activated_since': agg['logged_in_since'],
        'staff_users': agg['staff'],
        'analytics_excluded_users': len(excluded_user_hashes()),
    }


def _validate_override(result):
    if not isinstance(result, dict):
        raise ImproperlyConfigured('user_accounts_counter must return a dict')
    for key, value in result.items():
        if key not in FIELDS:
            raise ImproperlyConfigured(f'user_accounts_counter returned unknown field {key!r}')
        if type(value) is not int or value < 0:
            raise ImproperlyConfigured(
                f'user_accounts_counter field {key!r} must be a non-negative int'
            )


def counts(since):
    result = default_counts(since)
    dotted = settings.HELM_ANALYTICS.get('user_accounts_counter')
    if dotted:
        override = import_string(dotted)(since)
        _validate_override(override)
        result.update(override)
    return result
