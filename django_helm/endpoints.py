"""The single auth and envelope layer for every endpoint the SDK exposes to Helm.

Design rule 3: no endpoint implements its own auth, body checks, CSRF
handling or cache headers. A view is a plain ``fn(request, body) -> dict``
wrapped with ``helm_endpoint(query)``; everything else lives here.

Order of checks: key configured (unset means the endpoint is off: 404 for any
method), method, key presented and equal, body is a
JSON object, ``query`` matches, ``project`` matches. Every response, error
or not, is marked ``Cache-Control: no-store``. The view never touches
``request.user`` or the session.
"""
import hmac
import json
from datetime import datetime, timezone as dt_timezone
from functools import wraps

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

try:  # Django 5.1+: keep LoginRequiredMiddleware from redirecting Helm to a login page
    from django.contrib.auth.decorators import login_not_required
except ImportError:  # pragma: no cover - older Django
    def login_not_required(view):
        return view

_BEARER = 'Bearer '


class BadRequest(Exception):
    """Raised by an endpoint function; the message is safe to show Helm (HTTP 400).

    Any other exception is a server fault and propagates as a 500.
    """


def presented_key(request):
    """Return the API key the caller presented, or None.

    ``Authorization: Bearer <key>`` wins over ``X-Helm-Api-Key``.
    """
    auth = request.META.get('HTTP_AUTHORIZATION', '')
    if auth.startswith(_BEARER):
        return auth[len(_BEARER):].strip() or None
    return request.META.get('HTTP_X_HELM_API_KEY') or None


def _no_store(response):
    response['Cache-Control'] = 'no-store'
    return response


def _error(message, status=400):
    return JsonResponse({'error': message}, status=status)


def _parse_body(request):
    """Return the body as a dict, or None if it is not a JSON object."""
    try:
        body = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def parse_timestamp(value, field):
    """Parse an ISO 8601 string; naive values are taken as UTC.

    Returns an aware UTC datetime, or a naive UTC one when ``USE_TZ`` is off.
    Raises ``BadRequest`` for anything that is not an ISO 8601 string.
    """
    message = f'{field} must be an ISO 8601 timestamp'
    if not isinstance(value, str):
        raise BadRequest(message)
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise BadRequest(message) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    if not settings.USE_TZ:
        parsed = parsed.astimezone(dt_timezone.utc).replace(tzinfo=None)
    return parsed


def helm_endpoint(query, json_dumps_params=None):
    """Decorate ``fn(request, body) -> dict`` as a Helm-facing JSON view.

    ``json_dumps_params`` is passed to ``JsonResponse`` for the success body.
    """

    def decorator(fn):
        @wraps(fn)
        def view(request):
            return _no_store(_handle(request, query, fn, json_dumps_params))

        return login_not_required(csrf_exempt(view))

    return decorator


def _handle(request, query, fn, json_dumps_params=None):
    config = settings.HELM_ANALYTICS
    configured = config.get('api_key')
    if not configured:
        return HttpResponse(status=404)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    presented = presented_key(request)
    if not presented or not hmac.compare_digest(
        presented.encode('utf-8'), str(configured).encode('utf-8')
    ):
        return HttpResponse(status=401)
    body = _parse_body(request)
    if body is None:
        return _error('body must be a JSON object')
    if body.get('query') != query:
        return _error(f"query must be '{query}'")
    if body.get('project') != config['project']:
        return _error('unknown project')
    try:
        return JsonResponse(fn(request, body), json_dumps_params=json_dumps_params)
    except BadRequest as exc:
        return _error(str(exc))
