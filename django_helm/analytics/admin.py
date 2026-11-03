"""Django admin integration for Helm analytics.

Add ``HelmUserAnalyticsInline`` to your ``UserAdmin.inlines`` to let staff set
a per-user inclusion override and note::

    from django_helm.analytics.admin import HelmUserAnalyticsInline

    class MyUserAdmin(UserAdmin):
        inlines = [*UserAdmin.inlines, HelmUserAnalyticsInline]

The exclusion rule admin registers itself automatically.
"""
import logging

from django.contrib import admin
from django.db import Error as DatabaseError
from django.template.response import TemplateResponse

from .models import AnalyticsExclusionRule, HelmUserAnalytics

PREVIEW_LIMIT = 50

logger = logging.getLogger('django_helm.analytics')


class HelmUserAnalyticsInline(admin.StackedInline):
    model = HelmUserAnalytics
    max_num = 1
    can_delete = False
    fields = ('inclusion', 'note')
    verbose_name_plural = 'Helm analytics'


@admin.register(AnalyticsExclusionRule)
class AnalyticsExclusionRuleAdmin(admin.ModelAdmin):
    list_display = ('pattern', 'field_path', 'enabled', 'matches', 'note', 'updated_at')
    list_editable = ('enabled',)
    actions = ['preview_matches']

    @admin.display(description='Matches')
    def matches(self, obj):
        try:
            return obj.matching_users().distinct().count()
        except DatabaseError:
            logger.warning('helm analytics: rule %s pattern rejected by database', obj.pk, exc_info=True)
            return 'error'

    @admin.action(description='Preview matches')
    def preview_matches(self, request, queryset):
        previews = []
        for rule in queryset:
            users = rule.matching_users().distinct()
            previews.append({
                'rule': rule,
                'total': users.count(),
                'values': list(users.values_list(rule.field_path, flat=True)[:PREVIEW_LIMIT]),
            })
        context = {
            **self.admin_site.each_context(request),
            'title': 'Preview matches',
            'previews': previews,
            'limit': PREVIEW_LIMIT,
        }
        return TemplateResponse(request, 'django_helm/preview_matches.html', context)
