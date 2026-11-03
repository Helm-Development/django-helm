import json
from unittest import mock
from datetime import datetime, timezone
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldError, ImproperlyConfigured
from django.test import Client

from django_helm.endpoints import BadRequest
from django_helm.analytics import user_accounts as ua
from django_helm.analytics.models import AnalyticsExclusionRule

pytestmark = pytest.mark.django_db
User = get_user_model()
SINCE = datetime(2026, 10, 1, tzinfo=timezone.utc)

def _mk(name, email='x@x.com', **kw):
    u = User.objects.create_user(name, email, 'p')
    for k, v in kw.items():
        setattr(u, k, v)
    u.save()
    return u

def test_default_counts():
    _mk('old_active', date_joined=datetime(2026, 1, 1, tzinfo=timezone.utc), last_login=datetime(2026, 10, 5, tzinfo=timezone.utc))
    _mk('new_never', date_joined=datetime(2026, 10, 2, tzinfo=timezone.utc))
    _mk('inactive_staff', is_active=False, is_staff=True, date_joined=datetime(2026, 1, 1, tzinfo=timezone.utc))
    _mk('excluded', email='e@acme.com', date_joined=datetime(2026, 1, 1, tzinfo=timezone.utc))
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    assert ua.default_counts(SINCE) == {
        'total_users': 4, 'active_users': 3, 'inactive_users': 1,
        'activated_users': 1, 'not_activated_users': 3,
        'created_since': 1, 'logged_in_since': 1, 'activated_since': 1,
        'staff_users': 1, 'analytics_excluded_users': 1,
    }

def my_counter(since):
    return {'activated_users': 42}

def bad_key_counter(since):
    return {'nope': 1}

def bad_value_counter(since):
    return {'activated_users': True}

def test_counter_override_merges(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'user_accounts_counter': f'{__name__}.my_counter'}
    out = ua.counts(SINCE)
    assert out['activated_users'] == 42 and out['total_users'] == 0

def non_dict_counter(since):
    return [1, 2]

def negative_counter(since):
    return {'activated_users': -1}

def full_counter(since):
    return {f: 7 for f in ua.FIELDS}

def partial_counter(since):
    return {'total_users': 5, 'active_users': 4, 'inactive_users': 1}

def test_override_supplying_all_fields_never_touches_user_table(settings, django_assert_num_queries):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'user_accounts_counter': f'{__name__}.full_counter'}
    with mock.patch.object(ua, 'default_counts', side_effect=AssertionError('defaults computed')):
        out = ua.counts(SINCE)
    assert out == {f: 7 for f in ua.FIELDS}

def test_override_supplies_some_fields_defaults_compute_the_rest(settings):
    _mk('a')
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'user_accounts_counter': f'{__name__}.partial_counter'}
    out = ua.counts(SINCE)
    assert (out['total_users'], out['active_users'], out['inactive_users']) == (5, 4, 1)
    assert out['not_activated_users'] == 1 and out['staff_users'] == 0

def test_missing_default_field_raises_improperly_configured_naming_field(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'user_accounts_counter': f'{__name__}.my_counter'}
    err = FieldError("Cannot resolve keyword 'is_staff' into field.")
    with mock.patch.object(ua, '_aggregate_defaults', side_effect=err):
        with pytest.raises(ImproperlyConfigured) as exc:
            ua.counts(SINCE)
    assert 'user_accounts_counter' in str(exc.value) and 'is_staff' in str(exc.value)

@pytest.mark.parametrize('name', ['bad_key_counter', 'bad_value_counter', 'non_dict_counter', 'negative_counter'])
def test_counter_override_rejects_bad_output(settings, name):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'user_accounts_counter': f'{__name__}.{name}'}
    with pytest.raises(ImproperlyConfigured):
        ua.counts(SINCE)

@pytest.mark.parametrize('raw', ['2026-10-01T00:00:00Z', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00'])
def test_parse_since_accepts_iso_and_assumes_utc(raw):
    assert ua.parse_since(raw) == SINCE

@pytest.mark.parametrize('raw', [None, 123, 'yesterday', ''])
def test_parse_since_rejects(raw):
    with pytest.raises(BadRequest):
        ua.parse_since(raw)

def _call(body):
    return Client().post('/helm/user_accounts', data=json.dumps(body), content_type='application/json',
                         HTTP_AUTHORIZATION='Bearer hwk_test')

def test_view_returns_all_ten_fields():
    resp = _call({'query': 'user_accounts', 'project': 'test-project', 'since': '2026-10-01T00:00:00Z'})
    assert resp.status_code == 200
    assert set(json.loads(resp.content)) == set(ua.FIELDS)

def test_view_missing_since_is_400():
    resp = _call({'query': 'user_accounts', 'project': 'test-project'})
    assert resp.status_code == 400
    assert json.loads(resp.content) == {'error': 'since must be an ISO 8601 timestamp'}

def test_parse_since_naive_utc_when_use_tz_false(settings):
    settings.USE_TZ = False
    parsed = ua.parse_since('2026-10-01T02:00:00+02:00')
    assert parsed.tzinfo is None and parsed == datetime(2026, 10, 1, 0, 0)
