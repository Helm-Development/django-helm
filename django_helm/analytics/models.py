from django.conf import settings
from django.db import models

INCLUDE_DEFAULT = 'default'
INCLUDE_ALWAYS = 'include'
EXCLUDE_ALWAYS = 'exclude'

INCLUSION_HELP_TEXT = (
    "**Default** — Helm's exclusion rules decide (excluded only if an enabled rule matches this user). "
    "**Always include** — count this user in analytics even if a rule matches them. "
    "**Always exclude** — never count this user, regardless of rules. "
    "Changes reach Helm on its next hourly sync and apply retroactively."
)


class HelmUserAnalytics(models.Model):
    """Per-user Helm analytics inclusion flag, kept off the customer's user table."""

    INCLUDE_DEFAULT = INCLUDE_DEFAULT
    INCLUDE_ALWAYS = INCLUDE_ALWAYS
    EXCLUDE_ALWAYS = EXCLUDE_ALWAYS
    INCLUSION_CHOICES = [
        (INCLUDE_DEFAULT, 'Default (rules decide)'),
        (INCLUDE_ALWAYS, 'Always include'),
        (EXCLUDE_ALWAYS, 'Always exclude'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='helm_analytics',
    )
    inclusion = models.CharField(
        max_length=16,
        choices=INCLUSION_CHOICES,
        default=INCLUDE_DEFAULT,
        help_text=INCLUSION_HELP_TEXT,
    )
    note = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'django_helm_user_analytics'
        verbose_name = 'Helm analytics'

    def __str__(self):
        return f'{self.user_id}: {self.inclusion}'
