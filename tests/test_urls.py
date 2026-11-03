from django.urls import reverse


def test_default_path_is_registered():
    assert reverse('helm_analytics_exclusion_list') == '/helm/analytics_exclusion_list'


def test_override_changes_registered_path(settings):
    from django_helm.urls import build_urlpatterns
    settings.HELM_ANALYTICS = {
        **settings.HELM_ANALYTICS,
        'paths': {'analytics_exclusion_list': '/custom/list'},
    }
    [pattern] = [p for p in build_urlpatterns() if p.name == 'helm_analytics_exclusion_list']
    assert str(pattern.pattern) == 'custom/list'
