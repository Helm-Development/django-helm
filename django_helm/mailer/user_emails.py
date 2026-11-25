"""Pure query layer behind the user-emails endpoint.

Helm asks which verified accounts appeared or changed address between
``since`` (inclusive) and ``until`` (exclusive) so its mailer can add contacts
and rename changed ones. The result carries emails and names only, never a
user id or a Helm hash.

Paging keeps rows that share the last timestamp together and advances the
cursor by one microsecond, so progress is guaranteed without an id in the
cursor. A page is also held under ``PAGE_BYTES`` of serialised JSON by
dropping whole tie groups from its end; Helm rejects bodies over 64 KiB. A
single tie group bigger than the budget is still returned whole, because
splitting it would lose rows.
"""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models.functions import Lower

from .models import UserEmailEvent
from .signals import normalize

ACCOUNTS_PAGE = 150
CHANGES_PAGE = 75
PAGE_BYTES = 56 * 1024
# Room for the envelope keys (project, generated_at, since, until, has_more,
# next_since) around the two lists.
ENVELOPE_BYTES = 1024
EVENT_RETENTION_DAYS = 90
RESERVED_SUFFIXES = ('.local', '.invalid', '.test', '.example', '.localhost')

_TICK = timedelta(microseconds=1)
_USER_FIELDS = ('email', 'first_name', 'last_name', 'date_joined')


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


def wire(row):
    """The row as it goes on the wire (``changed_at`` as ISO 8601)."""
    return {**row, 'changed_at': row['changed_at'].isoformat()}


def _row_bytes(row):
    # Same json.dumps defaults as JsonResponse, plus the ", " list separator.
    return len(json.dumps(wire(row), ensure_ascii=False).encode('utf-8')) + 2


def page_bytes(accounts, changes):
    """Upper bound on the serialised response size for these two lists."""
    return ENVELOPE_BYTES + sum(_row_bytes(r) for r in accounts) + sum(_row_bytes(r) for r in changes)


def _cut(rows, page):
    """Return (rows, has_more), keeping ties on the last kept timestamp together."""
    if len(rows) <= page:
        return rows, False
    boundary = rows[page - 1]['changed_at']
    kept = rows[:page]
    kept.extend(row for row in rows[page:] if row['changed_at'] == boundary)
    return kept, len(kept) < len(rows)


def _groups(rows):
    """Split rows (sorted by changed_at) into runs that share a timestamp."""
    groups = []
    for row in rows:
        if groups and groups[-1][0]['changed_at'] == row['changed_at']:
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def _fit(accounts, changes):
    """Drop whole tie groups from the end of accounts, then changes, until the
    page fits ``PAGE_BYTES``. The first group of each list always stays.
    Returns (accounts, accounts_trimmed, changes, changes_trimmed)."""
    lists = [_groups(accounts), _groups(changes)]
    sizes = [[sum(_row_bytes(r) for r in g) for g in groups] for groups in lists]
    total = ENVELOPE_BYTES + sum(map(sum, sizes))
    trimmed = [False, False]
    for i in (0, 1):
        while total > PAGE_BYTES and len(lists[i]) > 1:
            lists[i].pop()
            total -= sizes[i].pop()
            trimmed[i] = True
    flat = [[row for group in groups for row in group] for groups in lists]
    return flat[0], trimmed[0], flat[1], trimmed[1]


def _account(user, changed_at):
    return {
        'email': user['email'],
        'first_name': user.get('first_name', '') or '',
        'last_name': user.get('last_name', '') or '',
        'changed_at': changed_at,
    }


def collect(since, until):
    user_model = get_user_model()
    concrete = {f.name for f in user_model._meta.concrete_fields}
    fields = [name for name in _USER_FIELDS if name in concrete]
    users = user_model._default_manager.filter(is_active=True).exclude(email='')

    candidates = [
        _account(u, u['date_joined'])
        for u in users.filter(date_joined__gte=since, date_joined__lt=until).values(*fields)
    ]

    in_range = UserEmailEvent.objects.filter(occurred_at__gte=since, occurred_at__lt=until)

    activations = list(in_range.filter(kind=UserEmailEvent.KIND_ACTIVATED))
    wanted = {normalize(e.email) for e in activations} - {''}
    current = {}
    if wanted:
        rows = (
            users.annotate(helm_email_lower=Lower('email'))
            .filter(helm_email_lower__in=wanted)
            .order_by('pk')
            .values(*fields)
        )
        for row in rows:
            current.setdefault(normalize(row['email']), row)
    for event in activations:
        user = current.get(normalize(event.email))
        if user is not None:
            candidates.append(_account(user, event.occurred_at))

    changes = []
    for e in in_range.filter(kind=UserEmailEvent.KIND_EMAIL_CHANGED).order_by('occurred_at', 'pk'):
        if not is_reportable(e.email):
            continue
        if is_reportable(e.previous_email):
            changes.append({'previous_email': e.previous_email, 'email': e.email, 'changed_at': e.occurred_at})
        else:
            # Blank or placeholder to real: nothing to rename, so it is a new contact.
            candidates.append({'email': e.email, 'first_name': e.first_name, 'last_name': e.last_name,
                               'changed_at': e.occurred_at})

    earliest = {}
    for row in candidates:
        key = normalize(row['email'])
        if key not in earliest or row['changed_at'] < earliest[key]['changed_at']:
            earliest[key] = row
    accounts = sorted(
        (r for r in earliest.values() if is_reportable(r['email'])),
        key=lambda r: (r['changed_at'], r['email']),
    )

    accounts, accounts_more = _cut(accounts, ACCOUNTS_PAGE)
    changes, changes_more = _cut(changes, CHANGES_PAGE)
    accounts, accounts_trimmed, changes, changes_trimmed = _fit(accounts, changes)
    accounts_more = accounts_more or accounts_trimmed
    changes_more = changes_more or changes_trimmed

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
