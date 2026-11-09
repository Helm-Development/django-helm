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


def test_full_page_with_cyrillic_names_fits_under_64_kib():
    from django_helm.mailer.models import UserEmailEvent
    User.objects.bulk_create([
        User(username=f'u{i}', email=f'firstname.lastname{i:04d}@privaterelay.appleid.com',
             first_name='Александра', last_name='Монтгомери-Смит',
             date_joined=T0 + timedelta(seconds=i, microseconds=123456), is_active=True)
        for i in range(150)
    ])
    for i in range(75):
        UserEmailEvent.objects.create(kind='email_changed', previous_email=f'old.address{i:04d}@gmail.com',
                                      email=f'new.address{i:04d}@outlook.com', occurred_at=T0 + timedelta(seconds=i, microseconds=5))
    resp = _call({**BODY, 'since': T0.isoformat(), 'until': (T0 + timedelta(days=1)).isoformat()})
    data = json.loads(resp.content.decode('utf-8'))
    assert len(data['accounts']) == 150 and len(data['email_changes']) == 75
    assert 'Александра' in resp.content.decode('utf-8')  # sent as UTF-8, not \\u escapes
    assert len(resp.content) < 64 * 1024


def test_trimmed_page_body_stays_within_the_byte_budget():
    from django_helm.mailer.user_emails import PAGE_BYTES
    User.objects.bulk_create([
        User(username=f'u{i}', email=f'user{i}@example.org', first_name='Я' * 150, last_name='Ж' * 150,
             date_joined=T0 + timedelta(seconds=i), is_active=True)
        for i in range(150)
    ])
    resp = _call({**BODY, 'since': T0.isoformat(), 'until': (T0 + timedelta(days=1)).isoformat()})
    data = json.loads(resp.content.decode('utf-8'))
    assert data['has_more'] is True and 0 < len(data['accounts']) < 150
    assert len(resp.content) <= PAGE_BYTES
