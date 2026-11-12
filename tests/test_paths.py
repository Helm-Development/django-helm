from django_helm import paths


def test_default_constants():
    assert paths.USER_ACCOUNTS == '/helm/user_accounts'
    assert paths.ANALYTICS_EXCLUSION_LIST == '/helm/analytics_exclusion_list'
    assert paths.SOCIAL_POSTS == '/helm/social_posts'
    assert paths.DEFAULTS == {
        'user_accounts': '/helm/user_accounts',
        'analytics_exclusion_list': '/helm/analytics_exclusion_list',
        'social_posts': '/helm/social_posts',
    }


def test_resolve_uses_default_when_no_override(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'paths': {}}
    assert paths.resolve('user_accounts') == '/helm/user_accounts'


def test_resolve_uses_override(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'paths': {'analytics_exclusion_list': '/custom/list'}}
    assert paths.resolve('analytics_exclusion_list') == '/custom/list'


def test_resolve_unknown_name_raises():
    import pytest
    with pytest.raises(KeyError):
        paths.resolve('nope')


def test_resolve_normalizes_slashless_override(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'paths': {'analytics_exclusion_list': 'internal/exclusions'}}
    assert paths.resolve('analytics_exclusion_list') == '/internal/exclusions'


def test_resolve_with_paths_none_uses_defaults(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'paths': None}
    assert paths.resolve('user_accounts') == '/helm/user_accounts'


def test_social_posts_default_and_override(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'paths': {}}
    assert paths.resolve('social_posts') == '/helm/social_posts'
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'paths': {'social_posts': '/custom/posts'}}
    assert paths.resolve('social_posts') == '/custom/posts'
