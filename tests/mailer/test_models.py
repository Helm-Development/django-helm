import pytest
from django.utils import timezone

from django_helm.mailer.models import UserEmailEvent

pytestmark = pytest.mark.django_db


def test_table_name_and_defaults():
    ev = UserEmailEvent.objects.create(
        kind=UserEmailEvent.KIND_ACTIVATED, email='a@x.com', occurred_at=timezone.now(),
    )
    assert UserEmailEvent._meta.db_table == 'django_helm_user_email_event'
    assert ev.previous_email == '' and ev.first_name == '' and ev.last_name == ''


def test_occurred_at_is_indexed():
    assert UserEmailEvent._meta.get_field('occurred_at').db_index is True
