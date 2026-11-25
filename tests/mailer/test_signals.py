import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from django_helm.mailer import signals
from django_helm.mailer.models import UserEmailEvent

pytestmark = pytest.mark.django_db
User = get_user_model()

def _user(email, active=True, **kw):
    u = User.objects.create_user('u_' + email, email, 'p', **kw)
    if u.is_active != active:
        User.objects.filter(pk=u.pk).update(is_active=active)
        u.refresh_from_db()
    return u

def test_new_user_records_nothing():
    _user('a@x.com')
    assert UserEmailEvent.objects.count() == 0

def test_activation_records_activated_with_current_fields():
    u = _user('a@x.com', active=False, first_name='Ann')
    u.is_active = True; u.save()
    [ev] = UserEmailEvent.objects.all()
    assert (ev.kind, ev.email, ev.first_name, ev.previous_email) == ('activated', 'a@x.com', 'Ann', '')

def test_email_change_on_active_user_records_previous_and_new():
    u = _user('old@x.com')
    u.email = 'New@x.com'; u.save()
    [ev] = UserEmailEvent.objects.all()
    assert (ev.kind, ev.previous_email, ev.email) == ('email_changed', 'old@x.com', 'New@x.com')

def test_case_only_change_records_nothing():
    u = _user('a@x.com'); u.email = 'A@x.com'; u.save()
    assert UserEmailEvent.objects.count() == 0

def test_unchanged_save_records_nothing():
    u = _user('a@x.com'); u.first_name = 'Zed'; u.save()
    assert UserEmailEvent.objects.count() == 0

def test_email_change_on_inactive_user_records_nothing():
    u = _user('a@x.com', active=False); u.email = 'b@x.com'; u.save()
    assert UserEmailEvent.objects.count() == 0

def test_activation_and_change_in_one_save_records_only_activated():
    u = _user('a@x.com', active=False); u.is_active = True; u.email = 'b@x.com'; u.save()
    [ev] = UserEmailEvent.objects.all()
    assert (ev.kind, ev.email) == ('activated', 'b@x.com')

def test_deleting_user_removes_its_events():
    u = _user('a@x.com', active=False); u.is_active = True; u.save()
    u.email = 'B@x.com'; u.save()
    assert UserEmailEvent.objects.count() == 2
    u.delete()
    assert UserEmailEvent.objects.count() == 1  # the activated event carries a@x.com, not b@x.com
    assert UserEmailEvent.objects.get().email == 'a@x.com'


@pytest.mark.django_db(transaction=True)
def test_failed_save_records_nothing():
    """A save that fails at the database must not leave an event behind."""
    _user('taken@x.com')
    u = _user('old@x.com')
    u.email = 'new@x.com'
    u.username = 'u_taken@x.com'  # duplicate username: the UPDATE raises
    with pytest.raises(IntegrityError):
        u.save()
    assert UserEmailEvent.objects.count() == 0


def test_update_fields_without_email_or_is_active_records_nothing_and_skips_lookup():
    u = _user('old@x.com')
    u.email = 'stale@x.com'  # in-memory change that this save does not write
    u.last_login = timezone.now()
    with CaptureQueriesContext(connection) as ctx:
        u.save(update_fields=['last_login'])
    assert len(ctx.captured_queries) == 1  # the UPDATE only, no prior-state SELECT
    assert UserEmailEvent.objects.count() == 0


def test_update_fields_uses_stored_value_for_unwritten_field():
    u = _user('old@x.com')
    u.is_active = False  # stale in memory, not written by this save
    u.email = 'new@x.com'
    u.save(update_fields=['email'])
    [ev] = UserEmailEvent.objects.all()
    assert (ev.kind, ev.previous_email, ev.email) == ('email_changed', 'old@x.com', 'new@x.com')


def test_raw_save_records_nothing():
    u = _user('old@x.com', active=False)
    u.is_active = True
    u.email = 'new@x.com'
    u.save_base(raw=True)
    assert UserEmailEvent.objects.count() == 0


def test_user_model_without_names_still_records():
    """Names are read with getattr, so a user model without them works."""
    class NoNames:
        pk = 1
        email = 'a@x.com'
        is_active = True

    instance = NoNames()
    instance._helm_mailer_prior = {'email': 'a@x.com', 'is_active': False}
    signals.record_user_change(User, instance, created=False)
    [ev] = UserEmailEvent.objects.all()
    assert (ev.kind, ev.email, ev.first_name, ev.last_name) == ('activated', 'a@x.com', '', '')
