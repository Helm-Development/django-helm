from django.apps import apps
from django.core import checks
from django.db import models
from django.test.utils import isolate_apps

from django_helm.mailer.checks import check_user_model, user_model_errors


def test_check_is_registered_under_models_tag():
    assert apps.get_app_config('django_helm_mailer') is not None
    assert check_user_model in checks.registry.registry.get_checks()
    assert checks.registry.registry.tag_exists(checks.Tags.models)


def test_default_user_model_passes():
    assert check_user_model() == []
    ids = [e.id for e in checks.run_checks(tags=[checks.Tags.models])]
    assert 'django_helm.E001' not in ids


@isolate_apps('django_helm.mailer')
def test_user_model_missing_fields_is_an_error():
    class BareUser(models.Model):
        email = models.EmailField()

        class Meta:
            app_label = 'django_helm_mailer'

    [error] = user_model_errors(BareUser)
    assert error.id == 'django_helm.E001' and isinstance(error, checks.Error)
    assert 'is_active' in error.msg and 'date_joined' in error.msg


@isolate_apps('django_helm.mailer')
def test_user_model_without_names_passes():
    class NamelessUser(models.Model):
        email = models.EmailField()
        is_active = models.BooleanField(default=True)
        date_joined = models.DateTimeField()

        class Meta:
            app_label = 'django_helm_mailer'

    assert user_model_errors(NamelessUser) == []
