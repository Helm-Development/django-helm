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


def test_user_emails_default_path_is_registered():
    assert reverse('helm_user_emails') == '/helm/user_emails'


def test_route_absent_without_mailer_app(monkeypatch):
    from django.apps import apps as django_apps
    from django_helm.urls import build_urlpatterns
    real = django_apps.is_installed
    monkeypatch.setattr(django_apps, 'is_installed', lambda name: False if name == 'django_helm.mailer' else real(name))
    assert not [p for p in build_urlpatterns() if p.name == 'helm_user_emails']
