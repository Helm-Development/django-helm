"""The single auth and envelope layer for every endpoint the SDK exposes to Helm.

Design rule 3: no endpoint implements its own auth, body checks, CSRF
handling or cache headers. A view is a plain ``fn(request, body) -> dict``
wrapped with ``helm_endpoint(query)``; everything else lives here.

Order of checks: method, key configured, key presented and equal, body is a
JSON object, ``query`` matches, ``project`` matches. Every response, error
or not, is marked ``Cache-Control: no-store``. The view never touches
``request.user`` or the session.
"""
import hmac
import json
from functools import wraps

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

_BEARER = 'Bearer '


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


def helm_endpoint(query):
    """Decorate ``fn(request, body) -> dict`` as a Helm-facing JSON view."""

    def decorator(fn):
        @csrf_exempt
        @wraps(fn)
        def view(request):
            return _no_store(_handle(request, query, fn))

        return view

    return decorator


def _handle(request, query, fn):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    config = settings.HELM_ANALYTICS
    configured = config.get('api_key')
    if not configured:
        return HttpResponse(status=404)
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
        return JsonResponse(fn(request, body))
    except ValueError as exc:
        return _error(str(exc))
