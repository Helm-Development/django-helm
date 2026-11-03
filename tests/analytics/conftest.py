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
            'django_helm.analytics',
            'django.contrib.admin',
            'django.contrib.messages',
            'django.contrib.sessions',
        ],
        MIDDLEWARE=[
            'django.contrib.sessions.middleware.SessionMiddleware',
            'django.contrib.auth.middleware.AuthenticationMiddleware',
            'django.contrib.messages.middleware.MessageMiddleware',
        ],
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'APP_DIRS': True,
            'OPTIONS': {
                'context_processors': [
                    'django.template.context_processors.request',
                    'django.contrib.auth.context_processors.auth',
                    'django.contrib.messages.context_processors.messages',
                ],
            },
        }],
        ROOT_URLCONF='tests.urls',
        SECRET_KEY='test-secret-not-for-prod',
        USE_TZ=True,
        DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
        HELM_ANALYTICS={
            'project': 'test-project',
            'sas_token': 'sas_test',
            'api_base_url': 'https://helm.test',
            'user_hash_salt': 'test-salt-do-not-use-in-prod',
            'api_key': 'hwk_test',
            'paths': {},
            'deny_paths': [],
            'allow_paths': None,
            'ip_precision': 'full',
            'batch_size': 50,
            'batch_interval_s': 5.0,
            'queue_max': 10_000,
        },
    )
    django.setup()

