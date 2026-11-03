"""Django settings shim for analytics tests.

We don't depend on a real Django project — pytest-django configures a
minimal settings module via DJANGO_SETTINGS_MODULE. Middleware tests
need request/response objects + an authenticated User to exercise the
user_hash code path, so we wire up auth + the in-memory DB.

The settings themselves live in tests/manage_settings.py so that
`makemigrations` and the test run share one definition.
"""
import django
from django.conf import settings

from tests import manage_settings


def pytest_configure():
    if settings.configured:
        return
    settings.configure(
        **{k: v for k, v in vars(manage_settings).items() if k.isupper()}
    )
    django.setup()
