from unittest import mock

import pytest
from django.db import DatabaseError
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from django_helm.analytics.admin import AnalyticsExclusionRuleAdmin, HelmUserAnalyticsInline
from django_helm.analytics.models import AnalyticsExclusionRule, HelmUserAnalytics

pytestmark = pytest.mark.django_db
User = get_user_model()
TOOLTIP = ("**Default** — Helm's exclusion rules decide (excluded only if an enabled rule matches this user). "
           "**Always include** — count this user in analytics even if a rule matches them. "
           "**Always exclude** — never count this user, regardless of rules. "
           "Changes reach Helm on its next hourly sync and apply retroactively.")


def test_rule_admin_registered_with_expected_columns():
    ma = admin.site._registry[AnalyticsExclusionRule]
    assert isinstance(ma, AnalyticsExclusionRuleAdmin)
    assert ma.list_display == ('pattern', 'field_path', 'enabled', 'matches', 'note', 'updated_at')
    assert ma.list_editable == ('enabled',)
    assert 'preview_matches' in ma.actions


def test_matches_column_counts():
    User.objects.create_user('a', 'a@acme.com', 'p')
    User.objects.create_user('b', 'b@x.com', 'p')
    r = AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    assert AnalyticsExclusionRuleAdmin(AnalyticsExclusionRule, admin.site).matches(r) == 1


def test_inline_help_text_is_the_tooltip():
    assert HelmUserAnalytics._meta.get_field('inclusion').help_text == TOOLTIP
    assert HelmUserAnalyticsInline.max_num == 1 and HelmUserAnalyticsInline.can_delete is False


def test_preview_action_lists_values():
    su = User.objects.create_superuser('root', 'root@x.com', 'p')
    for i in range(60):
        User.objects.create_user(f'u{i}', f'u{i}@acme.com', 'p')
    r = AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    c = Client()
    c.force_login(su)
    url = reverse('admin:django_helm_analytics_analyticsexclusionrule_changelist')
    resp = c.post(url, {'action': 'preview_matches', '_selected_action': [r.pk]})
    html = resp.content.decode()
    assert resp.status_code == 200
    assert 'u0@acme.com' in html and '60' in html
    assert html.count('@acme.com') == 50


def test_preview_action_works_for_disabled_rule():
    su = User.objects.create_superuser('root', 'root@x.com', 'p')
    User.objects.create_user('a', 'a@acme.com', 'p')
    r = AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$', enabled=False)
    c = Client()
    c.force_login(su)
    url = reverse('admin:django_helm_analytics_analyticsexclusionrule_changelist')
    resp = c.post(url, {'action': 'preview_matches', '_selected_action': [r.pk]})
    assert resp.status_code == 200
    assert 'a@acme.com' in resp.content.decode()


def test_matches_counts_distinct_users_over_multivalued_path():
    from django.contrib.auth.models import Group
    u = User.objects.create_user('a', 'a@x.com', 'p')
    u.groups.add(Group.objects.create(name='team-one'), Group.objects.create(name='team-two'))
    r = AnalyticsExclusionRule.objects.create(pattern='^team', field_path='groups__name')
    assert AnalyticsExclusionRuleAdmin(AnalyticsExclusionRule, admin.site).matches(r) == 1


def test_preview_total_counts_distinct_users():
    from django.contrib.auth.models import Group
    su = User.objects.create_superuser('root', 'root@x.com', 'p')
    u = User.objects.create_user('a', 'a@x.com', 'p')
    u.groups.add(Group.objects.create(name='team-one'), Group.objects.create(name='team-two'))
    r = AnalyticsExclusionRule.objects.create(pattern='^team', field_path='groups__name')
    c = Client()
    c.force_login(su)
    url = reverse('admin:django_helm_analytics_analyticsexclusionrule_changelist')
    html = c.post(url, {'action': 'preview_matches', '_selected_action': [r.pk]}).content.decode()
    assert '1 matching user (' in html


def test_matches_returns_error_string_on_database_rejection():
    r = AnalyticsExclusionRule.objects.create(pattern='x')
    ma = AnalyticsExclusionRuleAdmin(AnalyticsExclusionRule, admin.site)
    with mock.patch.object(AnalyticsExclusionRule, 'matching_users', side_effect=DatabaseError('bad')):
        assert ma.matches(r) == 'error'


def test_preview_back_link_goes_to_rule_changelist():
    su = User.objects.create_superuser('root', 'root@x.com', 'p')
    r = AnalyticsExclusionRule.objects.create(pattern='x')
    c = Client()
    c.force_login(su)
    url = reverse('admin:django_helm_analytics_analyticsexclusionrule_changelist')
    html = c.post(url, {'action': 'preview_matches', '_selected_action': [r.pk]}).content.decode()
    assert f'<a href="{url}">Back to rules</a>' in html
