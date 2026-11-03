"""Plain settings module for the analytics test shim.

Single source of truth: tests/analytics/conftest.py builds its
settings.configure(...) call from the upper-case names here, and
`DJANGO_SETTINGS_MODULE=tests.manage_settings django-admin makemigrations`
uses it directly, so the two never drift.
"""
import os

DEBUG = True
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}
# Run the suite on PostgreSQL with HELM_TEST_DB_ENGINE=postgresql plus
# HELM_TEST_DB_NAME/USER/PASSWORD/HOST/PORT. Django creates test_<NAME>.
if os.environ.get('HELM_TEST_DB_ENGINE') == 'postgresql':
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ['HELM_TEST_DB_NAME'],
        'USER': os.environ.get('HELM_TEST_DB_USER', ''),
        'PASSWORD': os.environ.get('HELM_TEST_DB_PASSWORD', ''),
        'HOST': os.environ.get('HELM_TEST_DB_HOST', ''),
        'PORT': os.environ.get('HELM_TEST_DB_PORT', ''),
    }
INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'django_helm.analytics',
    'django.contrib.admin',
    'django.contrib.messages',
    'django.contrib.sessions',
]
MIDDLEWARE = [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'APP_DIRS': True,
    'OPTIONS': {
        'context_processors': [
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
        ],
    },
}]
ROOT_URLCONF = 'tests.urls'
SECRET_KEY = 'test-secret-not-for-prod'
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
HELM_ANALYTICS = {
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
}
