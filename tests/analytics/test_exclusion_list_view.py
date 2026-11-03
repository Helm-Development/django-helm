import json
import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from django_helm.analytics.models import AnalyticsExclusionRule
from django_helm.analytics.user_hash import derive_user_hash

pytestmark = pytest.mark.django_db
User = get_user_model()


def _call(client, body, key='hwk_test'):
    return client.post('/helm/analytics_exclusion_list', data=json.dumps(body),
                       content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {key}')


def test_returns_hashes_with_reasons():
    a = User.objects.create_user('a', 'a@acme.com', 'p')
    AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$', note='staff')
    resp = _call(Client(), {'query': 'analytics_exclusion_list', 'project': 'test-project'})
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data['project'] == 'test-project' and data['count'] == 1
    assert data['exclusions'] == [{'user_hash': derive_user_hash('test-project', a.pk, 'test-salt-do-not-use-in-prod'), 'reason': 'rule:staff'}]
    assert 'generated_at' in data and resp['Cache-Control'] == 'no-store'


def test_wrong_key_is_401():
    assert _call(Client(), {'query': 'analytics_exclusion_list', 'project': 'test-project'}, key='nope').status_code == 401


def test_wrong_query_is_400():
    assert _call(Client(), {'query': 'user_accounts', 'project': 'test-project'}).status_code == 400
