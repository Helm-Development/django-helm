"""Endpoint view served at /helm/user_emails when the mailer app is installed."""
from django.conf import settings
from django.utils import timezone

from django_helm.endpoints import BadRequest, helm_endpoint, parse_timestamp

from .user_emails import collect, purge_old_events


def _iso(value):
    return value.isoformat() if value is not None else None


def _with_iso(rows):
    return [{**row, 'changed_at': row['changed_at'].isoformat()} for row in rows]


@helm_endpoint('user_emails')
def user_emails(request, body):
    now = timezone.now()
    since = parse_timestamp(body.get('since'), 'since')
    if body.get('until') is not None:
        until = parse_timestamp(body['until'], 'until')
    else:
        until = now
    if since >= until:
        raise BadRequest('since must be before until')
    purge_old_events(now)
    data = collect(since, until)
    return {
        'project': settings.HELM_ANALYTICS['project'],
        'generated_at': now.isoformat(),
        'since': since.isoformat(),
        'until': until.isoformat(),
        'accounts': _with_iso(data['accounts']),
        'email_changes': _with_iso(data['email_changes']),
        'has_more': data['has_more'],
        'next_since': _iso(data['next_since']),
    }
