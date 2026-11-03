"""URL wiring for the endpoints Helm polls on a customer backend.

Customers add one line to their root URLconf::

    path('', include('django_helm.urls'))

Each endpoint is served at its default path (see ``django_helm.paths``) unless
the customer overrides it via ``HELM_ANALYTICS['paths']``, keyed by endpoint
name (for example ``{'analytics_exclusion_list': '/internal/exclusions'}``).
``urlpatterns`` is built once, at import.
"""
from django.urls import path

from django_helm import paths
from django_helm.analytics import views

VIEWS = [
    ('analytics_exclusion_list', views.exclusion_list),
]



def build_urlpatterns():
    """Resolve every endpoint path from the current settings."""
    return [
        path(paths.resolve(name).lstrip('/'), view, name=f'helm_{name}')
        for name, view in VIEWS
    ]


urlpatterns = build_urlpatterns()
