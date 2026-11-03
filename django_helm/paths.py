"""Default URL paths for the endpoints Helm polls on a customer backend.

Mirrored by Helm in apps/site_settings/registry.py (SDK_PATH_* placeholders). Change both.

Customers may override any path via ``HELM_ANALYTICS['paths']``, keyed by the
lower-case constant name (for example ``'user_accounts'``).
"""
from django.conf import settings

USER_ACCOUNTS = '/helm/user_accounts'
ANALYTICS_EXCLUSION_LIST = '/helm/analytics_exclusion_list'

DEFAULTS = {
    'user_accounts': USER_ACCOUNTS,
    'analytics_exclusion_list': ANALYTICS_EXCLUSION_LIST,
}


def resolve(name: str) -> str:
    """Return the configured path for ``name``, falling back to the default.

    Raises KeyError for an unknown name.
    """
    default = DEFAULTS[name]
    value = (settings.HELM_ANALYTICS.get('paths') or {}).get(name) or default
    return '/' + value.lstrip('/')
