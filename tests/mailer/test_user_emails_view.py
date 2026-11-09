import json
from datetime import datetime, timedelta, timezone as tz
import pytest
from django.contrib.auth import get_user_model
from django.test import Client

pytestmark = pytest.mark.django_db
User = get_user_model()
T0 = datetime(2026, 11, 1, tzinfo=tz.utc)
BODY = {'query': 'user_emails', 'project': 'test-project'}

def _call(body, key='hwk_test'):
    return Client().post('/helm/user_emails', data=json.dumps(body), content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {key}')

def test_response_shape_has_no_identifiers():
    u = User.objects.create_user('u', 'a@x.com', 'p', first_name='A', last_name='B')
    User.objects.filter(pk=u.pk).update(date_joined=T0 + timedelta(hours=1))
    resp = _call({**BODY, 'since': T0.isoformat(), 'until': (T0 + timedelta(days=1)).isoformat()})
    assert resp.status_code == 200 and resp['Cache-Control'] == 'no-store'
    data = json.loads(resp.content)
    assert set(data) == {'project', 'generated_at', 'since', 'until', 'accounts', 'email_changes', 'has_more', 'next_since'}
    assert data['accounts'] == [{'email': 'a@x.com', 'first_name': 'A', 'last_name': 'B', 'changed_at': (T0 + timedelta(hours=1)).isoformat()}]
    assert data['has_more'] is False and data['next_since'] is None
    assert 'hashed_id' not in resp.content.decode() and not {'id', 'pk', 'user_id'} & set(data['accounts'][0])

def test_until_defaults_to_now():
    resp = _call({**BODY, 'since': T0.isoformat()})
    assert resp.status_code == 200 and json.loads(resp.content)['until'] > T0.isoformat()

def test_missing_since_is_400():
    assert _call(BODY).status_code == 400

def test_since_after_until_is_400():
    resp = _call({**BODY, 'since': (T0 + timedelta(days=1)).isoformat(), 'until': T0.isoformat()})
    assert resp.status_code == 400 and json.loads(resp.content) == {'error': 'since must be before until'}

def test_wrong_key_is_401_and_wrong_query_is_400():
    assert _call({**BODY, 'since': T0.isoformat()}, key='nope').status_code == 401
    assert _call({**BODY, 'query': 'user_accounts', 'since': T0.isoformat()}).status_code == 400
