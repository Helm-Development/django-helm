import logging

from django.apps import apps


def test_app_is_installed_with_expected_label():
    cfg = apps.get_app_config('django_helm_analytics')
    assert cfg.name == 'django_helm.analytics'


def test_package_init_does_not_import_models():
    # Customers who only use the middleware never run migrate; the package must import without models.
    import inspect
    import django_helm.analytics
    lines = [l.strip() for l in inspect.getsource(django_helm.analytics).splitlines()]
    assert not any(l.startswith(('from .models', 'from . import models', 'from .views', 'from .admin')) for l in lines)


def test_init_warns_when_installed_and_api_key_empty(settings, caplog):
    import django_helm.analytics as analytics
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'api_key': ''}
    with caplog.at_level(logging.WARNING, logger='django_helm.analytics'):
        analytics.init()
    assert any('api_key' in r.getMessage() for r in caplog.records)


def test_init_silent_when_api_key_set(settings, caplog):
    import django_helm.analytics as analytics
    with caplog.at_level(logging.WARNING, logger='django_helm.analytics'):
        analytics.init()
    assert not caplog.records
