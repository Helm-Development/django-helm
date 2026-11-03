import json
import pytest
from django.test import RequestFactory

from django_helm.endpoints import helm_endpoint, presented_key

rf = RequestFactory()


@helm_endpoint('ping')
def ping(request, body):
    if body.get('boom'):
        raise ValueError('boom requested')
    return {'ok': True, 'extra': body.get('extra')}


def _post(body, key='hwk_test', header='bearer', raw=None):
    headers = {}
    if key is not None:
        if header == 'bearer':
            headers['HTTP_AUTHORIZATION'] = f'Bearer {key}'
        else:
            headers['HTTP_X_HELM_API_KEY'] = key
    data = raw if raw is not None else json.dumps(body)
    return rf.post('/helm/ping', data=data, content_type='application/json', **headers)


GOOD = {'query': 'ping', 'project': 'test-project'}


def test_ok_via_bearer():
    resp = ping(_post({**GOOD, 'extra': 1}))
    assert resp.status_code == 200
    assert json.loads(resp.content) == {'ok': True, 'extra': 1}
    assert resp['Cache-Control'] == 'no-store'


def test_ok_via_x_helm_api_key():
    assert ping(_post(GOOD, header='x')).status_code == 200


@pytest.mark.parametrize('key', ['wrong', '', None])
def test_bad_or_missing_key_is_401_empty(key):
    resp = ping(_post(GOOD, key=key))
    assert resp.status_code == 401 and resp.content == b''
    assert resp['Cache-Control'] == 'no-store'


def test_unset_key_is_404(settings):
    settings.HELM_ANALYTICS = {**settings.HELM_ANALYTICS, 'api_key': ''}
    resp = ping(_post(GOOD))
    assert resp.status_code == 404 and resp.content == b''
    assert resp['Cache-Control'] == 'no-store'


def test_get_is_405():
    resp = ping(rf.get('/helm/ping'))
    assert resp.status_code == 405
    assert resp['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('raw', ['[]', '"x"', 'null', 'not json', ''])
def test_non_object_body_is_400(raw):
    resp = ping(_post(None, raw=raw))
    assert resp.status_code == 400
    assert json.loads(resp.content) == {'error': 'body must be a JSON object'}
    assert resp['Cache-Control'] == 'no-store'


def test_wrong_query_is_400():
    resp = ping(_post({**GOOD, 'query': 'other'}))
    assert resp.status_code == 400 and json.loads(resp.content) == {'error': "query must be 'ping'"}
    assert resp['Cache-Control'] == 'no-store'


def test_wrong_project_is_400():
    resp = ping(_post({**GOOD, 'project': 'other'}))
    assert resp.status_code == 400 and json.loads(resp.content) == {'error': 'unknown project'}
    assert resp['Cache-Control'] == 'no-store'


def test_value_error_from_view_is_400():
    resp = ping(_post({**GOOD, 'boom': True}))
    assert resp.status_code == 400 and json.loads(resp.content) == {'error': 'boom requested'}
    assert resp['Cache-Control'] == 'no-store'


def test_view_is_csrf_exempt():
    assert getattr(ping, 'csrf_exempt', False) is True


def test_bearer_wins_over_header():
    req = rf.post('/x', HTTP_AUTHORIZATION='Bearer a', HTTP_X_HELM_API_KEY='b')
    assert presented_key(req) == 'a'


def test_presented_key_none_when_absent():
    assert presented_key(rf.post('/x')) is None
