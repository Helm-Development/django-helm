"""System check: the mailer needs a user model with the fields it reads.

The receivers and the endpoint read ``email``, ``is_active`` and
``date_joined`` on the user model. A custom user model without one of them
would fail on the first save or the first Helm call, so the check reports it
at startup instead. ``first_name`` and ``last_name`` are optional.
"""
from django.core import checks

REQUIRED_USER_FIELDS = ('email', 'is_active', 'date_joined')


def missing_user_fields(user_model):
    concrete = {field.name for field in user_model._meta.concrete_fields}
    return [name for name in REQUIRED_USER_FIELDS if name not in concrete]


def check_user_model(app_configs=None, **kwargs):
    from django.contrib.auth import get_user_model

    return user_model_errors(get_user_model())


def user_model_errors(user_model):
    missing = missing_user_fields(user_model)
    if not missing:
        return []
    return [checks.Error(
        f"django_helm.mailer needs the user model to have the concrete fields "
        f"{', '.join(REQUIRED_USER_FIELDS)}; {user_model._meta.label} lacks {', '.join(missing)}.",
        hint='Add the missing fields to your user model, or remove django_helm.mailer '
             'from INSTALLED_APPS.',
        obj=user_model,
        id='django_helm.E001',
    )]
