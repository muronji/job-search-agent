import django_filters
from django.db.models import Q

from .models import Job


class JobFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method='filter_search')
    industry = django_filters.CharFilter(field_name='industry', lookup_expr='icontains')
    location = django_filters.CharFilter(field_name='location', lookup_expr='icontains')
    date_posted_from = django_filters.DateFilter(field_name='date_posted', lookup_expr='gte')
    date_posted_to = django_filters.DateFilter(field_name='date_posted', lookup_expr='lte')
    salary_min = django_filters.NumberFilter(method='filter_salary_min')
    salary_max = django_filters.NumberFilter(method='filter_salary_max')
    gone = django_filters.BooleanFilter(method='filter_gone')

    class Meta:
        model = Job
        fields = [
            'seniority',
            'employment_type',
            'work_arrangement',
            'application_status',
            'source_listing_status',
            'source',
        ]

    def filter_search(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(
            Q(title__icontains=value)
            | Q(company__icontains=value)
            | Q(description__icontains=value)
            | Q(industry__icontains=value)
        )

    def filter_salary_min(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(salary_max__isnull=False, salary_max__gte=value)

    def filter_salary_max(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(salary_min__isnull=False, salary_min__lte=value)

    def filter_gone(self, queryset, name, value):
        if value:
            return queryset.filter(source_listing_status=Job.SourceListingStatus.EXPIRED)
        return queryset.exclude(source_listing_status=Job.SourceListingStatus.EXPIRED)
