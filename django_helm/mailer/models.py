from django.db import models


class UserEmailEvent(models.Model):
    """An email-address lifecycle event (account activated, address changed)."""

    KIND_ACTIVATED = 'activated'
    KIND_EMAIL_CHANGED = 'email_changed'
    KIND_CHOICES = [
        (KIND_ACTIVATED, 'Activated'),
        (KIND_EMAIL_CHANGED, 'Email changed'),
    ]

    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    email = models.EmailField(max_length=254)
    previous_email = models.EmailField(max_length=254, blank=True, default='')
    first_name = models.CharField(max_length=150, blank=True, default='')
    last_name = models.CharField(max_length=150, blank=True, default='')
    occurred_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'django_helm_user_email_event'
        ordering = ['occurred_at', 'pk']

    def __str__(self):
        return f'{self.kind} at {self.occurred_at:%Y-%m-%d %H:%M:%S}'
