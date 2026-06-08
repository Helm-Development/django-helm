import pytest
from django.test import override_settings


def test_module_exports():
    """The analytics package exposes the documented public surface."""
    import django_helm.analytics as analytics
    assert hasattr(analytics, 'ApiHitMiddleware')
    assert hasattr(analytics, 'derive_user_hash')
    assert hasattr(analytics, 'metrics')
    assert hasattr(analytics, 'init')


def test_init_is_callable_with_required_settings():
    """init() runs without raising when HELM_ANALYTICS is present."""
    from django_helm.analytics import init
    init()  # Should not raise


def test_init_raises_when_settings_missing(settings):
    """If HELM_ANALYTICS is missing, init() raises a clear error."""
    delattr(settings, 'HELM_ANALYTICS')
    from django_helm.analytics import init
    with pytest.raises(RuntimeError, match='HELM_ANALYTICS'):
        init()


def test_init_raises_when_required_keys_missing(settings):
    """If a required key is missing from HELM_ANALYTICS, init() raises."""
    settings.HELM_ANALYTICS = {'project': 'p'}  # missing sas_token, api_base_url, user_hash_salt
    from django_helm.analytics import init
    with pytest.raises(RuntimeError, match='sas_token|api_base_url|user_hash_salt'):
        init()
