"""Account counts served at /helm/user_accounts.

Defaults rely only on fields Django's ``AbstractUser`` guarantees. A customer
whose user model differs supplies ``HELM_ANALYTICS['user_accounts_counter']``,
a dotted path to a callable ``(since) -> dict`` whose values override defaults.
"""
from datetime import datetime, timezone

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.db.models import Count, Q
from django.utils.module_loading import import_string

from django_helm.endpoints import BadRequest

from .exclusions import excluded_user_hashes

FIELDS = (
    'total_users', 'active_users', 'inactive_users', 'activated_users',
    'not_activated_users', 'created_since', 'logged_in_since',
    'activated_since', 'staff_users', 'analytics_excluded_users',
)


def parse_since(value):
    """Parse an ISO 8601 string; naive values are taken as UTC.

    Returns an aware UTC datetime, or a naive UTC one when ``USE_TZ`` is off.
    Raises ``BadRequest`` for anything that is not an ISO 8601 string.
    """
    message = 'since must be an ISO 8601 timestamp'
    if not isinstance(value, str):
        raise BadRequest(message)
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise BadRequest(message) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if not settings.USE_TZ:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


# Output field -> aggregate names it is derived from.
_DEPENDS = {
    'total_users': ('total',),
    'active_users': ('active',),
    'inactive_users': ('total', 'active'),
    'activated_users': ('activated',),
    'not_activated_users': ('total', 'activated'),
    'created_since': ('created_since',),
    'logged_in_since': ('logged_in_since',),
    'activated_since': ('logged_in_since',),
    'staff_users': ('staff',),
}


def _aggregates(since):
    return {
        'total': Count('pk'),
        'active': Count('pk', filter=Q(is_active=True)),
        'activated': Count('pk', filter=Q(last_login__isnull=False)),
        'created_since': Count('pk', filter=Q(date_joined__gte=since)),
        'logged_in_since': Count('pk', filter=Q(last_login__gte=since)),
        'staff': Count('pk', filter=Q(is_staff=True)),
    }


def _derive(field, agg):
    if field == 'inactive_users':
        return agg['total'] - agg['active']
    if field == 'not_activated_users':
        return agg['total'] - agg['activated']
    return agg[_DEPENDS[field][0]]


def _aggregate_defaults(since, fields):
    """Compute only ``fields`` (a subset of ``_DEPENDS``) in one aggregate query."""
    needed = {name for f in fields for name in _DEPENDS[f]}
    available = _aggregates(since)
    agg = get_user_model()._default_manager.aggregate(**{n: available[n] for n in needed})
    return {f: _derive(f, agg) for f in fields}


def default_counts(since):
    result = _aggregate_defaults(since, list(_DEPENDS))
    result['analytics_excluded_users'] = len(excluded_user_hashes())
    return result


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
    """Return all ten fields: the customer's counter first, defaults for the rest."""
    result = {}
    dotted = settings.HELM_ANALYTICS.get('user_accounts_counter')
    if dotted:
        override = import_string(dotted)(since)
        _validate_override(override)
        result.update(override)
    missing = [f for f in _DEPENDS if f not in result]
    if missing:
        try:
            result.update(_aggregate_defaults(since, missing))
        except FieldError as exc:
            raise ImproperlyConfigured(
                f"Cannot compute default account counts ({', '.join(missing)}) "
                f"for your user model: {exc}. Supply these fields from "
                "HELM_ANALYTICS['user_accounts_counter']."
            ) from exc
    if 'analytics_excluded_users' not in result:
        result['analytics_excluded_users'] = len(excluded_user_hashes())
    return result
