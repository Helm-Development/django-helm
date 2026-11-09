from django.apps import AppConfig


class MailerConfig(AppConfig):
    name = 'django_helm.mailer'
    label = 'django_helm_mailer'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_delete, pre_save

        from . import signals

        user_model = get_user_model()
        pre_save.connect(
            signals.record_user_change, sender=user_model,
            dispatch_uid='django_helm_mailer_pre_save',
        )
        post_delete.connect(
            signals.forget_deleted_user, sender=user_model,
            dispatch_uid='django_helm_mailer_post_delete',
        )
