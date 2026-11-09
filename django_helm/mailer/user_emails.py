"""Pure query layer behind the user-emails endpoint.

Helm asks which verified accounts appeared or changed address between
``since`` and ``until`` so its mailer can add contacts and rename changed
ones. The result carries emails and names only, never a user id or a Helm hash.

Paging keeps rows that share the last timestamp together and advances the
cursor by one microsecond, so progress is guaranteed without an id in the
cursor.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model

from .models import UserEmailEvent
from .signals import normalize

ACCOUNTS_PAGE = 200
CHANGES_PAGE = 100
EVENT_RETENTION_DAYS = 90
RESERVED_SUFFIXES = ('.local', '.invalid', '.test', '.example', '.localhost')

_TICK = timedelta(microseconds=1)


def is_reportable(email):
    """True for a real-looking address whose domain is not a reserved placeholder."""
    if not email or '@' not in email:
        return False
    domain = email.rsplit('@', 1)[1].strip().lower()
    return bool(domain) and not domain.endswith(RESERVED_SUFFIXES)


def purge_old_events(now):
    """Delete events older than the retention window; return how many went."""
    cutoff = now - timedelta(days=EVENT_RETENTION_DAYS)
    deleted, _ = UserEmailEvent.objects.filter(occurred_at__lt=cutoff).delete()
    return deleted


def _cut(rows, page):
    """Return (rows, has_more), keeping ties on the last kept timestamp together."""
    if len(rows) <= page:
        return rows, False
    boundary = rows[page - 1]['changed_at']
    kept = rows[:page]
    kept.extend(row for row in rows[page:] if row['changed_at'] == boundary)
    return kept, True


def _account(user, changed_at):
    return {
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'changed_at': changed_at,
    }


def collect(since, until):
    users = get_user_model()._default_manager.filter(is_active=True).exclude(email='')

    candidates = [
        _account(u, u.date_joined)
        for u in users.filter(date_joined__gte=since, date_joined__lt=until)
    ]

    activations = UserEmailEvent.objects.filter(
        kind=UserEmailEvent.KIND_ACTIVATED,
        occurred_at__gte=since,
        occurred_at__lt=until,
    )
    for event in activations:
        user = users.filter(email__iexact=event.email).first()
        if user is not None:
            candidates.append(_account(user, event.occurred_at))

    earliest = {}
    for row in candidates:
        key = normalize(row['email'])
        if key not in earliest or row['changed_at'] < earliest[key]['changed_at']:
            earliest[key] = row
    accounts = sorted(
        (r for r in earliest.values() if is_reportable(r['email'])),
        key=lambda r: (r['changed_at'], r['email']),
    )

    changes = [
        {'previous_email': e.previous_email, 'email': e.email, 'changed_at': e.occurred_at}
        for e in UserEmailEvent.objects.filter(
            kind=UserEmailEvent.KIND_EMAIL_CHANGED,
            occurred_at__gte=since,
            occurred_at__lt=until,
        ).order_by('occurred_at', 'pk')
        if is_reportable(e.email) and is_reportable(e.previous_email)
    ]

    accounts, accounts_more = _cut(accounts, ACCOUNTS_PAGE)
    changes, changes_more = _cut(changes, CHANGES_PAGE)

    cursors = []
    if accounts_more:
        cursors.append(accounts[-1]['changed_at'] + _TICK)
    if changes_more:
        cursors.append(changes[-1]['changed_at'] + _TICK)

    return {
        'accounts': accounts,
        'email_changes': changes,
        'has_more': accounts_more or changes_more,
        'next_since': min(cursors) if cursors else None,
    }
