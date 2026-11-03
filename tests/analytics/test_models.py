import pytest
from django.contrib.auth import get_user_model
from django.apps import apps

from django_helm.analytics.models import HelmUserAnalytics

pytestmark = pytest.mark.django_db
User = get_user_model()


def test_user_analytics_defaults_and_table():
    u = User.objects.create_user('a', 'a@x.com', 'p')
    row = HelmUserAnalytics.objects.create(user=u)
    assert row.inclusion == 'default'
    assert u.helm_analytics == row
    assert HelmUserAnalytics._meta.db_table == 'django_helm_user_analytics'


def test_inclusion_help_text_is_the_tooltip():
    from django_helm.analytics.models import INCLUSION_HELP_TEXT
    assert HelmUserAnalytics._meta.get_field('inclusion').help_text == INCLUSION_HELP_TEXT
    assert INCLUSION_HELP_TEXT.startswith('**Default** — ') and INCLUSION_HELP_TEXT.endswith('apply retroactively.')


def test_inclusion_choices_exact():
    assert HelmUserAnalytics.INCLUSION_CHOICES == [
        ('default', 'Default (rules decide)'),
        ('include', 'Always include'),
        ('exclude', 'Always exclude'),
    ]


def test_every_sdk_table_is_prefixed():
    for model in apps.get_app_config('django_helm_analytics').get_models():
        assert model._meta.db_table.startswith('django_helm_'), model
