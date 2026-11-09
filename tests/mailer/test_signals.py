import pytest
from django.contrib.auth import get_user_model
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

def test_case_only_change_is_recorded():
    u = _user('a@x.com'); u.email = 'A@x.com'; u.save()
    assert UserEmailEvent.objects.filter(kind='email_changed').count() == 1

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
