import re
import time
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, override_settings


def _make_response(status=200):
    return HttpResponse(status=status, content=b'ok')


@pytest.fixture(autouse=True)
def reset_batcher_calls():
    from django_helm.analytics import batcher
    batcher._reset_worker_for_tests()
    yield
    batcher._reset_worker_for_tests()


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def captured_enqueues():
    """Patch enqueue so we can inspect what the middleware tried to record."""
    with patch('django_helm.analytics.middleware.enqueue') as mock:
        yield mock


@pytest.fixture
def db_user(db):
    User = get_user_model()
    return User.objects.create_user(username='alice', password='pw')


class TestDenyList:
    def test_health_path_denied(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/healthz/')
        request.user = MagicMock(is_authenticated=False)
        mw(request)
        assert not captured_enqueues.called

    def test_static_denied(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/static/app.css')
        request.user = MagicMock(is_authenticated=False)
        mw(request)
        assert not captured_enqueues.called

    @override_settings(HELM_ANALYTICS={
        'project': 'p', 'sas_token': 't', 'api_base_url': 'https://h.test',
        'user_hash_salt': 's', 'deny_paths': [r'^/skip/'],
        'allow_paths': None, 'ip_precision': 'full',
        'batch_size': 50, 'batch_interval_s': 5.0, 'queue_max': 100,
    })
    def test_custom_deny_paths_appended(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/skip/foo')
        request.user = MagicMock(is_authenticated=False)
        mw(request)
        assert not captured_enqueues.called


class TestOptOut:
    def test_dnt_header_skips_recording(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/foo/', HTTP_DNT='1')
        request.user = MagicMock(is_authenticated=False)
        mw(request)
        assert not captured_enqueues.called

    def test_x_helm_optout_header_skips_recording(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/foo/', HTTP_X_HELM_OPTOUT='1')
        request.user = MagicMock(is_authenticated=False)
        mw(request)
        assert not captured_enqueues.called


class TestRecording:
    def test_basic_request_recorded(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response(200))
        request = rf.get('/api/recipes/', HTTP_X_HELM_INSTALLATION_ID='abc-123', REMOTE_ADDR='192.0.2.1')
        request.user = MagicMock(is_authenticated=False)
        mw(request)
        assert captured_enqueues.called
        hit = captured_enqueues.call_args[0][0]
        assert hit['method'] == 'GET'
        assert hit['status_code'] == 200
        assert hit['installation_id'] == 'abc-123'
        assert hit['client_ip'] == '192.0.2.1'
        assert isinstance(hit['duration_ms'], int)
        assert hit['duration_ms'] >= 0

    def test_path_template_from_resolver_match(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/api/recipes/42/')
        request.user = MagicMock(is_authenticated=False)
        # Simulate a resolver_match attached by Django's URL dispatcher
        request.resolver_match = MagicMock(route='api/recipes/<int:id>/')
        mw(request)
        hit = captured_enqueues.call_args[0][0]
        assert hit['path_template'] == 'api/recipes/<int:id>/'

    def test_path_template_falls_back_to_literal_path(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/unmatched/path/')
        request.user = MagicMock(is_authenticated=False)
        # No resolver_match → use literal path
        mw(request)
        hit = captured_enqueues.call_args[0][0]
        assert hit['path_template'] == '/unmatched/path/'

    def test_path_template_truncated_to_255(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        long_path = '/' + 'a' * 300
        request = rf.get(long_path)
        request.user = MagicMock(is_authenticated=False)
        mw(request)
        hit = captured_enqueues.call_args[0][0]
        assert len(hit['path_template']) <= 255

    def test_session_id_header_captured(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/foo/', HTTP_X_HELM_SESSION_ID='sess-1')
        request.user = MagicMock(is_authenticated=False)
        mw(request)
        hit = captured_enqueues.call_args[0][0]
        assert hit['session_id'] == 'sess-1'

    def test_app_version_and_platform_headers_captured(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/foo/',
                         HTTP_X_HELM_APP_VERSION='1.2.3',
                         HTTP_X_HELM_PLATFORM='ios')
        request.user = MagicMock(is_authenticated=False)
        mw(request)
        hit = captured_enqueues.call_args[0][0]
        assert hit['app_version'] == '1.2.3'
        assert hit['platform'] == 'ios'


class TestUserHash:
    def test_authenticated_user_hash_derived(self, rf, captured_enqueues, db_user):
        from django_helm.analytics.middleware import ApiHitMiddleware
        from django_helm.analytics.user_hash import derive_user_hash
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/foo/')
        request.user = db_user
        mw(request)
        hit = captured_enqueues.call_args[0][0]
        expected = derive_user_hash('test-project', db_user.id, 'test-salt-do-not-use-in-prod')
        assert hit['user_hash'] == expected

    def test_anonymous_user_no_hash(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/foo/')
        request.user = MagicMock(is_authenticated=False)
        mw(request)
        hit = captured_enqueues.call_args[0][0]
        assert hit.get('user_hash') in (None, '')

    def test_inbound_user_hash_header_ignored(self, rf, captured_enqueues):
        """Middleware never trusts inbound X-Helm-User-Hash — clients could forge it."""
        from django_helm.analytics.middleware import ApiHitMiddleware
        mw = ApiHitMiddleware(lambda req: _make_response())
        request = rf.get('/foo/', HTTP_X_HELM_USER_HASH='attacker-supplied')
        request.user = MagicMock(is_authenticated=False)
        mw(request)
        hit = captured_enqueues.call_args[0][0]
        assert hit.get('user_hash') != 'attacker-supplied'


class TestFailureIsolation:
    def test_exception_in_enqueue_does_not_break_response(self, rf):
        """If enqueue raises, the host response is returned unchanged."""
        from django_helm.analytics.middleware import ApiHitMiddleware
        with patch('django_helm.analytics.middleware.enqueue', side_effect=RuntimeError('boom')):
            mw = ApiHitMiddleware(lambda req: _make_response(201))
            request = rf.get('/foo/')
            request.user = MagicMock(is_authenticated=False)
            resp = mw(request)
            assert resp.status_code == 201

    def test_exception_during_hash_derivation_does_not_break_response(self, rf, captured_enqueues):
        from django_helm.analytics.middleware import ApiHitMiddleware
        with patch('django_helm.analytics.middleware.derive_user_hash',
                   side_effect=RuntimeError('hash failure')):
            mw = ApiHitMiddleware(lambda req: _make_response(200))
            request = rf.get('/foo/')
            authed_user = MagicMock(is_authenticated=True, id=99)
            request.user = authed_user
            resp = mw(request)
            assert resp.status_code == 200


class TestAllowPaths:
    @pytest.fixture
    def allow_settings(self):
        from django.test import override_settings
        return override_settings(HELM_ANALYTICS={
            'project': 'p', 'sas_token': 't', 'api_base_url': 'https://h.test',
            'user_hash_salt': 's', 'deny_paths': [],
            'allow_paths': [r'^/api/'],   # anchored to start
            'ip_precision': 'full',
            'batch_size': 50, 'batch_interval_s': 5.0, 'queue_max': 100,
        })

    def test_allow_path_matches(self, rf, captured_enqueues, allow_settings):
        """A path matching an allow pattern is recorded."""
        from django_helm.analytics.middleware import ApiHitMiddleware
        with allow_settings:
            mw = ApiHitMiddleware(lambda req: _make_response())
            request = rf.get('/api/foo/')
            request.user = MagicMock(is_authenticated=False)
            mw(request)
            assert captured_enqueues.called

    def test_allow_path_anchored_not_substring(self, rf, captured_enqueues, allow_settings):
        """Allow patterns must be start-anchored — /internal/api/ does NOT match ^/api/."""
        from django_helm.analytics.middleware import ApiHitMiddleware
        with allow_settings:
            mw = ApiHitMiddleware(lambda req: _make_response())
            request = rf.get('/internal/api/x/')
            request.user = MagicMock(is_authenticated=False)
            mw(request)
            assert not captured_enqueues.called

    def test_allow_path_no_match_skipped(self, rf, captured_enqueues, allow_settings):
        """A path not matching any allow pattern is skipped."""
        from django_helm.analytics.middleware import ApiHitMiddleware
        with allow_settings:
            mw = ApiHitMiddleware(lambda req: _make_response())
            request = rf.get('/home/')
            request.user = MagicMock(is_authenticated=False)
            mw(request)
            assert not captured_enqueues.called
