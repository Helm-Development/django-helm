from django.apps import apps


def test_app_label_and_name():
    cfg = apps.get_app_config('django_helm_mailer')
    assert cfg.name == 'django_helm.mailer'


def test_mailer_package_does_not_import_analytics():
    import inspect

    import django_helm.mailer
    import django_helm.mailer.models

    for mod in (django_helm.mailer, django_helm.mailer.models):
        assert 'django_helm.analytics' not in inspect.getsource(mod)
