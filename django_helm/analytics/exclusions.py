"""Resolve which users Helm should exclude from analytics.

Resolution order:

1. Users flagged ``inclusion == 'exclude'`` are excluded, reason ``'flag'``.
2. Each enabled ``AnalyticsExclusionRule``, in ``id`` order, excludes the users
   it matches. A user already recorded keeps its earlier reason. The reason is
   ``rule:<note or pattern>``.
3. Users flagged ``inclusion == 'include'`` are removed, beating flags and rules.
4. Each remaining pk is turned into a ``user_hash`` and the output is sorted by it.

Settings are read at call time and nothing is cached.
"""
from typing import Any

from django.conf import settings

from django_helm.analytics.models import AnalyticsExclusionRule, HelmUserAnalytics
from django_helm.analytics.user_hash import derive_user_hash


def excluded_users() -> dict[Any, str]:
    """Map excluded user pk to the reason: ``'flag'`` or ``'rule:<note or pattern>'``."""
    excluded: dict[Any, str] = {}

    flagged = HelmUserAnalytics.objects.filter(inclusion=HelmUserAnalytics.EXCLUDE_ALWAYS)
    for pk in flagged.values_list('user_id', flat=True):
        excluded[pk] = 'flag'

    for rule in AnalyticsExclusionRule.objects.filter(enabled=True).order_by('id'):
        reason = f'rule:{rule.note or rule.pattern}'
        for pk in rule.matching_users().values_list('pk', flat=True):
            excluded.setdefault(pk, reason)

    always_included = HelmUserAnalytics.objects.filter(inclusion=HelmUserAnalytics.INCLUDE_ALWAYS)
    for pk in always_included.values_list('user_id', flat=True):
        excluded.pop(pk, None)

    return excluded


def excluded_user_hashes() -> list[dict]:
    """Return ``[{'user_hash', 'reason'}, ...]`` for excluded users, sorted by hash."""
    cfg = settings.HELM_ANALYTICS
    rows = [
        {'user_hash': derive_user_hash(cfg['project'], pk, cfg['user_hash_salt']), 'reason': reason}
        for pk, reason in excluded_users().items()
    ]
    return sorted(rows, key=lambda row: row['user_hash'])
