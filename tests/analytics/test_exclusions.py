import pytest
from django.contrib.auth import get_user_model

from django_helm.analytics.exclusions import excluded_user_hashes, excluded_users
from django_helm.analytics.models import AnalyticsExclusionRule, HelmUserAnalytics
from django_helm.analytics.user_hash import derive_user_hash

pytestmark = pytest.mark.django_db
User = get_user_model()


def _u(name, email):
    return User.objects.create_user(name, email, 'p')


def _h(user):
    return derive_user_hash('test-project', user.pk, 'test-salt-do-not-use-in-prod')


def test_default_user_with_no_rule_is_not_excluded():
    _u('a', 'a@acme.com')
    assert excluded_users() == {}


def test_rule_excludes_matching_default_users():
    a = _u('a', 'a@acme.com'); _u('b', 'b@other.com')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$', note='staff domain')
    assert excluded_users() == {a.pk: 'rule:staff domain'}


def test_rule_reason_falls_back_to_pattern_when_no_note():
    a = _u('a', 'a@acme.com')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    assert excluded_users() == {a.pk: 'rule:@acme\\.com$'}


def test_disabled_rule_is_ignored():
    _u('a', 'a@acme.com')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$', enabled=False)
    assert excluded_users() == {}


def test_always_include_beats_rule():
    a = _u('a', 'a@acme.com'); b = _u('b', 'b@acme.com')
    HelmUserAnalytics.objects.create(user=a, inclusion='include')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    assert excluded_users() == {b.pk: 'rule:@acme\\.com$'}


def test_always_exclude_without_rule():
    a = _u('a', 'a@x.com')
    HelmUserAnalytics.objects.create(user=a, inclusion='exclude')
    assert excluded_users() == {a.pk: 'flag'}


def test_flag_reason_wins_over_rule_and_lowest_rule_wins_over_later():
    a = _u('a', 'a@acme.com'); b = _u('b', 'b@acme.com')
    HelmUserAnalytics.objects.create(user=a, inclusion='exclude')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$', note='first')
    AnalyticsExclusionRule.objects.create(pattern='^b@', note='second')
    assert excluded_users() == {a.pk: 'flag', b.pk: 'rule:first'}


def test_custom_field_path():
    a = _u('qa_bot', 'a@x.com'); _u('real', 'r@x.com')
    AnalyticsExclusionRule.objects.create(pattern='^qa_', field_path='username')
    assert excluded_users() == {a.pk: 'rule:^qa_'}


def test_hashes_are_sorted_and_match_middleware_derivation():
    users = [_u(f'u{i}', f'u{i}@acme.com') for i in range(5)]
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$', note='n')
    out = excluded_user_hashes()
    assert [o['user_hash'] for o in out] == sorted(_h(u) for u in users)
    assert all(o['reason'] == 'rule:n' for o in out)
