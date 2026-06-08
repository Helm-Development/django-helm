"""ApiHitMiddleware — observes every request and enqueues an ApiHit for Helm.

Failure-isolated: any exception in the analytics path is swallowed so the
host application's response is never affected.
"""
import logging
import re
import time

from django.conf import settings

from .batcher import enqueue
from .user_hash import derive_user_hash


_DEFAULT_DENY = [
    r"^/health/?$",
    r"^/healthz/?$",
    r"^/readyz/?$",
    r"^/ping/?$",
    r"^/favicon\.ico$",
    r"^/static/",
    r"^/media/",
]

_logger = logging.getLogger('django_helm.analytics')


class ApiHitMiddleware:
    """Records one ApiHit per non-denied, non-opted-out request.

    Add to MIDDLEWARE after AuthenticationMiddleware so request.user is set."""

    def __init__(self, get_response):
        self.get_response = get_response
        cfg = settings.HELM_ANALYTICS
        custom_deny = cfg.get('deny_paths') or []
        all_deny = _DEFAULT_DENY + list(custom_deny)
        self._deny_patterns = [re.compile(p) for p in all_deny]
        allow = cfg.get('allow_paths')
        self._allow_patterns = [re.compile(p) for p in allow] if allow else None
        self._project_slug = cfg['project']
        self._salt = cfg['user_hash_salt']

    def __call__(self, request):
        skip = self._should_skip(request)
        start = time.perf_counter()
        response = self.get_response(request)
        if not skip:
            try:
                self._record(request, response, start)
            except Exception:
                _logger.warning('helm analytics: record failed', exc_info=True)
        return response

    def _should_skip(self, request) -> bool:
        path = request.path
        if request.headers.get('DNT') == '1':
            return True
        if request.headers.get('X-Helm-OptOut') == '1':
            return True
        for pat in self._deny_patterns:
            if pat.match(path):
                return True
        if self._allow_patterns is not None:
            if not any(p.search(path) for p in self._allow_patterns):
                return True
        return False

    def _record(self, request, response, start) -> None:
        path_template = self._resolve_path_template(request)
        duration_ms = int((time.perf_counter() - start) * 1000)
        user_hash = self._derive_hash_if_authed(request)

        hit = {
            'method': request.method[:8],
            'path_template': path_template,
            'status_code': response.status_code,
            'duration_ms': duration_ms,
            'client_ip': self._client_ip(request),
            'installation_id': request.headers.get('X-Helm-Installation-Id'),
            'session_id': request.headers.get('X-Helm-Session-Id'),
            'app_version': request.headers.get('X-Helm-App-Version'),
            'platform': request.headers.get('X-Helm-Platform'),
            'user_hash': user_hash,
            'occurred_at': self._now_iso(),
        }
        enqueue(hit)

    def _resolve_path_template(self, request) -> str:
        match = getattr(request, 'resolver_match', None)
        if match is not None and getattr(match, 'route', None):
            return match.route[:255]
        return request.path[:255]

    def _derive_hash_if_authed(self, request):
        user = getattr(request, 'user', None)
        if user is None or not getattr(user, 'is_authenticated', False):
            return None
        return derive_user_hash(self._project_slug, user.id, self._salt)

    def _client_ip(self, request) -> str:
        fwd = request.META.get('HTTP_X_FORWARDED_FOR')
        if fwd:
            return fwd.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')

    def _now_iso(self) -> str:
        from django.utils import timezone
        return timezone.now().isoformat()
