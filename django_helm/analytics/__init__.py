"""django_helm.analytics — passive backend instrumentation for Helm.

Public surface:
- ApiHitMiddleware — add to MIDDLEWARE
- init() — call once in settings.py to validate config (optional but recommended)
- derive_user_hash — hash helper for login responses and similar
- metrics — process-local counters
"""
import logging

from . import metrics
from .middleware import ApiHitMiddleware
from .user_hash import derive_user_hash


_logger = logging.getLogger('django_helm.analytics')

REQUIRED_KEYS = ('project', 'sas_token', 'api_base_url', 'user_hash_salt')


def init() -> None:
    """Validate that HELM_ANALYTICS is configured. Call from settings.py.

    Raises RuntimeError with a clear message if anything is missing.
    """
    from django.conf import settings
    cfg = getattr(settings, 'HELM_ANALYTICS', None)
    if cfg is None:
        raise RuntimeError(
            "HELM_ANALYTICS settings dict is required. See django-helm README §Analytics."
        )
    missing = [k for k in REQUIRED_KEYS if not cfg.get(k)]
    if missing:
        raise RuntimeError(
            f"HELM_ANALYTICS missing required keys: {', '.join(missing)}"
        )
    if 'django_helm.analytics' in settings.INSTALLED_APPS and not cfg.get('api_key'):
        _logger.warning(
            'HELM_ANALYTICS["api_key"] is empty; every django_helm endpoint will answer 404 until it is set'
        )


__all__ = ['ApiHitMiddleware', 'derive_user_hash', 'init', 'metrics']
