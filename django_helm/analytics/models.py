from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist, FieldError, ValidationError
from django.db import Error as DatabaseError
from django.db import connection, models, transaction

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


class AnalyticsExclusionRule(models.Model):
    """Regex rule that excludes matching users from Helm analytics."""

    TEXT_FIELD_TYPES = (models.CharField, models.TextField)

    pattern = models.CharField(
        max_length=255,
        help_text=(
            "Case-insensitive regular expression in your database's syntax. "
            'Tested against the database when you save.'
        ),
    )
    field_path = models.CharField(
        max_length=128,
        default='email',
        help_text='Django lookup path on your user model, e.g. email, username, profile__company.',
    )
    enabled = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'django_helm_exclusion_rule'
        ordering = ['id']

    def __str__(self):
        return f'{self.field_path} ~* {self.pattern}'

    def matching_users(self):
        return get_user_model()._default_manager.filter(**{f'{self.field_path}__iregex': self.pattern})

    def clean(self):
        super().clean()
        self._check_field_path()
        self._check_pattern()

    def _check_field_path(self):
        error = self._field_path_error()
        if error:
            raise ValidationError({'field_path': error})
        try:
            get_user_model()._default_manager.filter(**{f'{self.field_path}__regex': '.'})
        except FieldError as exc:
            raise ValidationError({'field_path': str(exc)})

    def _field_path_error(self):
        """Walk the path through the user model; return a message or None."""
        model = get_user_model()
        segments = self.field_path.split('__')
        for i, name in enumerate(segments):
            try:
                field = model._meta.get_field(name)
            except FieldDoesNotExist:
                return f"'{name}' is not a field on {model.__name__}."
            if i < len(segments) - 1:
                model = field.related_model
                if model is None:
                    return f"'{name}' is not a relation, so '__' cannot follow it."
        if not isinstance(field, self.TEXT_FIELD_TYPES):
            return f"'{self.field_path}' is not a text field, so a regular expression cannot match it."
        return None

    def _check_pattern(self):
        # The database is the only judge of the pattern: the regex that is
        # tested here is the regex that runs. Row-independent probe: the engine compiles the regex even with no
        # table rows. Savepoint so a rejected statement does not poison the
        # caller's transaction. 'iregex' is the lookup matching_users() runs.
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(f'SELECT {self._iregex_sql()}', ['x', self.pattern])
        except DatabaseError as exc:
            raise ValidationError({'pattern': f"Your database rejected this pattern: {exc}"})

    @staticmethod
    def _iregex_sql():
        """SQL for ``%s <iregex> %s`` as the ORM builds it for this vendor."""
        operator = connection.operators.get('iregex')
        if operator is None:  # vendors such as Oracle expose regex_lookup() instead
            return connection.ops.regex_lookup('iregex')
        return f"{connection.ops.lookup_cast('iregex', 'CharField')} {operator}"
