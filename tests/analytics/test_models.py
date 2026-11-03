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


# --- AnalyticsExclusionRule ---------------------------------------------------
from unittest import mock

from django.core.exceptions import ValidationError

from django_helm.analytics.models import AnalyticsExclusionRule


def test_rule_defaults_and_table():
    r = AnalyticsExclusionRule(pattern='@acme\\.com$')
    r.full_clean()
    r.save()
    assert r.field_path == 'email' and r.enabled is True
    assert AnalyticsExclusionRule._meta.db_table == 'django_helm_exclusion_rule'


def test_rule_help_texts_exact():
    meta = AnalyticsExclusionRule._meta
    assert meta.get_field('pattern').help_text == (
        "Case-insensitive regular expression in your database's syntax. "
        'Tested against the database when you save.'
    )
    assert meta.get_field('field_path').help_text == (
        'Django lookup path on your user model, e.g. email, username, profile__company.'
    )


def test_rule_matching_is_case_insensitive():
    a = User.objects.create_user('a', 'Alice@ACME.com', 'p')
    User.objects.create_user('b', 'bob@other.com', 'p')
    r = AnalyticsExclusionRule.objects.create(pattern='@acme\\.com$')
    assert list(r.matching_users()) == [a]


def test_clean_rejects_bad_regex():
    with pytest.raises(ValidationError) as exc:
        AnalyticsExclusionRule(pattern='(unclosed').full_clean()
    assert 'pattern' in exc.value.message_dict


def test_clean_rejects_unknown_field_path():
    with pytest.raises(ValidationError) as exc:
        AnalyticsExclusionRule(pattern='x', field_path='no_such_field').full_clean()
    assert 'field_path' in exc.value.message_dict


def test_clean_rejects_non_text_field_path():
    with pytest.raises(ValidationError) as exc:
        AnalyticsExclusionRule(pattern='1', field_path='id').full_clean()
    assert 'field_path' in exc.value.message_dict


def test_clean_rejects_non_relational_intermediate_segment():
    with pytest.raises(ValidationError) as exc:
        AnalyticsExclusionRule(pattern='x', field_path='email__foo').full_clean()
    assert 'field_path' in exc.value.message_dict


def test_clean_accepts_relation_path():
    # auth.User has groups__name; a relation path must validate and match.
    from django.contrib.auth.models import Group
    g = Group.objects.create(name='Staff Testers')
    u = User.objects.create_user('a', 'a@x.com', 'p')
    u.groups.add(g)
    User.objects.create_user('b', 'b@x.com', 'p')
    r = AnalyticsExclusionRule(pattern='^staff', field_path='groups__name')
    r.full_clean()
    r.save()
    assert list(r.matching_users()) == [u]


def test_clean_survives_db_rejection_inside_transaction():
    """The database check is what catches engine-specific bad patterns.

    On SQLite the regex engine is Python ``re``, so the Python check always
    rejects first. Patch it out to force the statement to reach the database.
    On PostgreSQL the database check is the only thing standing between an
    admin and a runtime error, and a rejected statement must not poison the
    surrounding transaction. A user row must exist, because the engine only
    evaluates the regex against rows it actually scans.
    """
    User.objects.create_user('a', 'a@x.com', 'p')
    with mock.patch('django_helm.analytics.models.re.compile', return_value=object()):
        with pytest.raises(ValidationError) as exc:
            AnalyticsExclusionRule(pattern='(unclosed').full_clean()
    assert 'pattern' in exc.value.message_dict
    assert User.objects.count() == 1
