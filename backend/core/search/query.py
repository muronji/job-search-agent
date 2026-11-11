"""Translate a SearchIntent into a Django ORM queryset."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from core.models import Job
from core.search.intent import SearchIntent


def intent_to_queryset(intent: SearchIntent) -> Job.objects:
    """Return a Job queryset matching the given intent."""
    qs = Job.objects.all().order_by("-date_discovered", "-id")

    if intent.keywords:
        value = intent.keywords
        qs = qs.filter(
            Q(title__icontains=value)
            | Q(company__icontains=value)
            | Q(description__icontains=value)
            | Q(industry__icontains=value)
            | Q(location__icontains=value)
        )

    for word in intent.exclude_keywords:
        qs = qs.exclude(
            Q(title__icontains=word)
            | Q(company__icontains=word)
            | Q(description__icontains=word)
        )

    if intent.job_titles:
        q = Q()
        for title in intent.job_titles:
            q |= Q(title__icontains=title)
        qs = qs.filter(q)

    if intent.industries:
        q = Q()
        for industry in intent.industries:
            q |= Q(industry__icontains=industry)
        qs = qs.filter(q)

    if intent.seniority_levels:
        qs = qs.filter(seniority__in=intent.seniority_levels)

    if intent.employment_types:
        qs = qs.filter(employment_type__in=intent.employment_types)

    if intent.work_arrangements:
        qs = qs.filter(work_arrangement__in=intent.work_arrangements)

    if intent.locations:
        q = Q()
        for location in intent.locations:
            q |= Q(location__icontains=location)
        qs = qs.filter(q)

    if intent.salary_min is not None:
        qs = qs.filter(salary_max__isnull=False, salary_max__gte=intent.salary_min)

    if intent.salary_max is not None:
        qs = qs.filter(salary_min__isnull=False, salary_min__lte=intent.salary_max)

    if intent.posted_within_days is not None:
        cutoff = timezone.now().date() - timedelta(days=intent.posted_within_days)
        qs = qs.filter(date_posted__gte=cutoff)

    return qs
