from datetime import datetime, timedelta, timezone as tz
import pytest
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
    for i in range(101):
        UserEmailEvent.objects.create(kind='email_changed', previous_email=f'o{i}@x.com', email=f'n{i}@x.com', occurred_at=T0 + timedelta(minutes=i))
    _joined('acct@x.com', T0 + timedelta(hours=10))
    out = ue.collect(T0, T1)
    assert len(out['email_changes']) == 100 and out['has_more'] is True
    assert out['next_since'] == T0 + timedelta(minutes=99, microseconds=1)
    assert [a['email'] for a in out['accounts']] == ['acct@x.com']  # the uncut list is returned in full

def test_purge_old_events():
    UserEmailEvent.objects.create(kind='activated', email='a@x.com', occurred_at=T0 - timedelta(days=91))
    UserEmailEvent.objects.create(kind='activated', email='b@x.com', occurred_at=T0 - timedelta(days=89))
    assert ue.purge_old_events(T0) == 1 and UserEmailEvent.objects.get().email == 'b@x.com'

@pytest.mark.parametrize('email,ok', [('a@x.com', True), ('', False), ('nope', False), ('a@b.local', False), ('a@b.LOCALHOST', False), ('a@privaterelay.appleid.com', True)])
def test_is_reportable(email, ok):
    assert ue.is_reportable(email) is ok
