import json
from datetime import datetime, timedelta, timezone as tz
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.contrib.auth import get_user_model
from django.utils import timezone
from django_helm.mailer import user_emails as ue
from django_helm.mailer.models import UserEmailEvent

pytestmark = pytest.mark.django_db
User = get_user_model()
T0 = datetime(2026, 11, 1, tzinfo=tz.utc)
T1 = T0 + timedelta(days=1)

def _joined(email, when, active=True, **kw):
    u = User.objects.create_user('u_' + email, email, 'p', **kw)
    User.objects.filter(pk=u.pk).update(date_joined=when, is_active=active)
    return u

def test_accounts_from_date_joined_only_active_with_email():
    _joined('a@x.com', T0 + timedelta(hours=1), first_name='A')
    _joined('b@x.com', T0 + timedelta(hours=2), active=False)
    _joined('', T0 + timedelta(hours=3))
    _joined('c@x.com', T1 + timedelta(hours=1))
    out = ue.collect(T0, T1)
    assert [a['email'] for a in out['accounts']] == ['a@x.com']
    assert out['accounts'][0] == {'email': 'a@x.com', 'first_name': 'A', 'last_name': '', 'changed_at': T0 + timedelta(hours=1)}
    assert out['has_more'] is False and out['next_since'] is None

def test_activated_event_adds_current_user_row_and_dedupes():
    u = _joined('a@x.com', T0 - timedelta(days=30))
    UserEmailEvent.objects.create(kind='activated', email='A@X.COM', occurred_at=T0 + timedelta(hours=5))
    UserEmailEvent.objects.create(kind='activated', email='gone@x.com', occurred_at=T0 + timedelta(hours=6))
    out = ue.collect(T0, T1)
    assert [a['email'] for a in out['accounts']] == ['a@x.com']
    assert out['accounts'][0]['changed_at'] == T0 + timedelta(hours=5)

def test_reserved_domains_are_never_reported():
    _joined('x@siwa.invalid.tastyspread.local', T0 + timedelta(hours=1))
    UserEmailEvent.objects.create(kind='email_changed', previous_email='a@x.com', email='b@foo.invalid', occurred_at=T0 + timedelta(hours=2))
    out = ue.collect(T0, T1)
    assert out['accounts'] == [] and out['email_changes'] == []

def test_siwa_merge_sequence_reports_one_change():
    dup = _joined('real@x.com', T0 - timedelta(days=5)); orig = _joined('orig@x.com', T0 - timedelta(days=9))
    dup.email = 'siwa-merged-1@invalid.tastyspread.local'; dup.save()
    orig.email = 'real@x.com'; orig.save()
    dup.delete()
    now = timezone.now()  # the receivers stamp events with the real clock
    out = ue.collect(now - timedelta(days=1), now + timedelta(days=1))
    assert [(c['previous_email'], c['email']) for c in out['email_changes']] == [('orig@x.com', 'real@x.com')]

def test_ties_are_kept_together_and_cursor_advances():
    same = T0 + timedelta(hours=1)
    User.objects.bulk_create([User(username=f'u{i}', email=f'u{i}@x.com', date_joined=same, is_active=True) for i in range(300)])
    _joined('late@x.com', T0 + timedelta(hours=2))
    out = ue.collect(T0, T1)
    assert len(out['accounts']) == 300 and out['has_more'] is True
    assert out['next_since'] == same + timedelta(microseconds=1)
    out2 = ue.collect(out['next_since'], T1)
    assert [a['email'] for a in out2['accounts']] == ['late@x.com'] and out2['has_more'] is False

def test_changes_page_cut_sets_next_since_to_min_over_cut_lists():
    for i in range(76):
        UserEmailEvent.objects.create(kind='email_changed', previous_email=f'o{i}@x.com', email=f'n{i}@x.com', occurred_at=T0 + timedelta(minutes=i))
    _joined('acct@x.com', T0 + timedelta(hours=10))
    out = ue.collect(T0, T1)
    assert len(out['email_changes']) == 75 and out['has_more'] is True
    assert out['next_since'] == T0 + timedelta(minutes=74, microseconds=1)
    assert [a['email'] for a in out['accounts']] == ['acct@x.com']  # the uncut list is returned in full

def test_purge_old_events():
    UserEmailEvent.objects.create(kind='activated', email='a@x.com', occurred_at=T0 - timedelta(days=91))
    UserEmailEvent.objects.create(kind='activated', email='b@x.com', occurred_at=T0 - timedelta(days=89))
    assert ue.purge_old_events(T0) == 1 and UserEmailEvent.objects.get().email == 'b@x.com'

@pytest.mark.parametrize('email,ok', [('a@x.com', True), ('', False), ('nope', False), ('a@b.local', False), ('a@b.LOCALHOST', False), ('a@privaterelay.appleid.com', True)])
def test_is_reportable(email, ok):
    assert ue.is_reportable(email) is ok


def test_page_sizes():
    assert (ue.ACCOUNTS_PAGE, ue.CHANGES_PAGE, ue.PAGE_BYTES) == (150, 75, 56 * 1024)


def test_both_lists_cut_next_since_is_the_earlier_cut():
    User.objects.bulk_create([User(username=f'a{i}', email=f'a{i}@x.com', date_joined=T0 + timedelta(minutes=i), is_active=True) for i in range(151)])
    for i in range(76):
        UserEmailEvent.objects.create(kind='email_changed', previous_email=f'o{i}@x.com', email=f'n{i}@x.com', occurred_at=T0 + timedelta(seconds=i))
    out = ue.collect(T0, T1)
    assert len(out['accounts']) == 150 and len(out['email_changes']) == 75 and out['has_more'] is True
    assert out['next_since'] == T0 + timedelta(seconds=74, microseconds=1)  # changes cut first


def test_activated_event_for_user_now_inactive_is_skipped():
    _joined('a@x.com', T0 - timedelta(days=30), active=False)
    UserEmailEvent.objects.create(kind='activated', email='a@x.com', occurred_at=T0 + timedelta(hours=1))
    assert ue.collect(T0, T1)['accounts'] == []


def test_activation_lookups_cost_a_constant_number_of_queries():
    User.objects.bulk_create([User(username=f'u{i}', email=f'u{i}@x.com', date_joined=T0 - timedelta(days=30), is_active=True) for i in range(40)])
    for i in range(40):
        UserEmailEvent.objects.create(kind='activated', email=f'U{i}@X.com', occurred_at=T0 + timedelta(minutes=i))
    with CaptureQueriesContext(connection) as ctx:
        out = ue.collect(T0, T1)
    assert len(out['accounts']) == 40
    assert len(ctx.captured_queries) <= 4


@pytest.mark.parametrize('previous', ['', 'merged-1@invalid.tastyspread.local'])
def test_change_from_blank_or_reserved_address_is_reported_as_an_account(previous):
    UserEmailEvent.objects.create(kind='email_changed', previous_email=previous, email='real@x.com',
                                  first_name='Ann', last_name='Lee', occurred_at=T0 + timedelta(hours=3))
    out = ue.collect(T0, T1)
    assert out['email_changes'] == []
    assert out['accounts'] == [{'email': 'real@x.com', 'first_name': 'Ann', 'last_name': 'Lee', 'changed_at': T0 + timedelta(hours=3)}]


def test_all_tied_rows_over_the_count_come_back_whole_without_more():
    """151 rows at one timestamp, each large: one page, over budget, has_more False."""
    same = T0 + timedelta(hours=1)
    User.objects.bulk_create([User(username=f'u{i}', email=f'u{i}@x.com', first_name='Я' * 150, last_name='Ж' * 150,
                                   date_joined=same, is_active=True) for i in range(151)])
    out = ue.collect(T0, T1)
    assert len(out['accounts']) == 151
    assert out['has_more'] is False and out['next_since'] is None
    assert ue.page_bytes(out['accounts'], out['email_changes']) > ue.PAGE_BYTES  # documented limitation


def test_over_budget_page_is_trimmed_at_a_tie_boundary():
    # 75 groups of two accounts each with long Cyrillic names: about 1.3 KB a row, over the budget.
    users = []
    for g in range(75):
        for j in range(2):
            users.append(User(username=f'u{g}_{j}', email=f'user{g}.{j}@example.org', first_name='Я' * 150,
                              last_name='Ж' * 150, date_joined=T0 + timedelta(minutes=g), is_active=True))
    User.objects.bulk_create(users)
    out = ue.collect(T0, T1)
    accounts = out['accounts']
    assert 0 < len(accounts) < 150 and len(accounts) % 2 == 0  # whole groups only
    assert ue.page_bytes(accounts, out['email_changes']) <= ue.PAGE_BYTES
    last = accounts[-1]['changed_at']
    assert out['has_more'] is True and out['next_since'] == last + timedelta(microseconds=1)
    first_trimmed = last + timedelta(minutes=1)
    out2 = ue.collect(out['next_since'], T1)
    assert [a['changed_at'] for a in out2['accounts'][:2]] == [first_trimmed, first_trimmed]


def test_trimming_falls_to_the_changes_list_once_accounts_are_one_group():
    same = T0 + timedelta(hours=1)
    User.objects.bulk_create([User(username=f'u{i}', email=f'u{i}@x.com', first_name='Я' * 150, last_name='Ж' * 150,
                                   date_joined=same, is_active=True) for i in range(40)])
    for i in range(75):
        UserEmailEvent.objects.create(kind='email_changed', previous_email=f'o{i}' + 'x' * 200 + '@x.com',
                                      email=f'n{i}' + 'y' * 200 + '@x.com', occurred_at=T0 + timedelta(minutes=i))
    out = ue.collect(T0, T1)
    assert len(out['accounts']) == 40  # the first group is never dropped
    assert 0 < len(out['email_changes']) < 75
    assert ue.page_bytes(out['accounts'], out['email_changes']) <= ue.PAGE_BYTES
    assert out['has_more'] is True
    assert out['next_since'] == out['email_changes'][-1]['changed_at'] + timedelta(microseconds=1)
