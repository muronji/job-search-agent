from django.contrib import admin

from .models import Job, JobSource, UserPreference


@admin.register(JobSource)
class JobSourceAdmin(admin.ModelAdmin):
    list_display = [
        'name',
        'source_type',
        'access_method',
        'is_active',
        'last_ingestion_status',
        'last_ingested_at',
        'created_at',
    ]
    list_filter = ['is_active', 'source_type', 'access_method', 'last_ingestion_status']
    search_fields = ['name']


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = [
        'title',
        'company',
        'location',
        'source',
        'application_status',
        'source_listing_status',
        'date_discovered',
    ]
    list_filter = [
        'application_status',
        'source_listing_status',
        'employment_type',
        'work_arrangement',
        'source',
    ]
    search_fields = ['title', 'company', 'description']


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'min_salary', 'salary_currency', 'created_at']
