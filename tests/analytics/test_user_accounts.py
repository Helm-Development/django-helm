import json
from datetime import datetime, timezone
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import Client

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

@pytest.mark.parametrize('name', ['bad_key_counter', 'bad_value_counter'])
def test_counter_override_rejects_bad_output(settings, name):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'user_accounts_counter': f'{__name__}.{name}'}
    with pytest.raises(ImproperlyConfigured):
        ua.counts(SINCE)

@pytest.mark.parametrize('raw', ['2026-10-01T00:00:00Z', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00'])
def test_parse_since_accepts_iso_and_assumes_utc(raw):
    assert ua.parse_since(raw) == SINCE

@pytest.mark.parametrize('raw', [None, 123, 'yesterday', ''])
def test_parse_since_rejects(raw):
    with pytest.raises(ValueError):
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
