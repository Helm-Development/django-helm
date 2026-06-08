"""Django settings shim for analytics tests.

We don't depend on a real Django project — pytest-django configures a
minimal settings module via DJANGO_SETTINGS_MODULE. Middleware tests
need request/response objects + an authenticated User to exercise the
user_hash code path, so we wire up auth + the in-memory DB.
"""
import django
from django.conf import settings


def pytest_configure():
    if settings.configured:
        return
    settings.configure(
        DEBUG=True,
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': ':memory:',
            },
        },
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
        ],
        MIDDLEWARE=[],
        ROOT_URLCONF=__name__,
        SECRET_KEY='test-secret-not-for-prod',
        USE_TZ=True,
        DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
        HELM_ANALYTICS={
            'project': 'test-project',
            'sas_token': 'sas_test',
            'api_base_url': 'https://helm.test',
            'user_hash_salt': 'test-salt-do-not-use-in-prod',
            'deny_paths': [],
            'allow_paths': None,
            'ip_precision': 'full',
            'batch_size': 50,
            'batch_interval_s': 5.0,
            'queue_max': 10_000,
        },
    )
    django.setup()


urlpatterns = []  # ROOT_URLCONF needs to point somewhere that imports cleanly
