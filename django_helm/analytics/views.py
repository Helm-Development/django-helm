"""Helm-facing analytics endpoints. Only decorated endpoint functions live here."""
from django.conf import settings
from django.utils import timezone

from django_helm.endpoints import helm_endpoint

from .exclusions import excluded_user_hashes


@helm_endpoint('analytics_exclusion_list')
def exclusion_list(request, body):
    rows = excluded_user_hashes()
    return {
        'project': settings.HELM_ANALYTICS['project'],
        'generated_at': timezone.now().isoformat(),
        'count': len(rows),
        'exclusions': rows,
    }
