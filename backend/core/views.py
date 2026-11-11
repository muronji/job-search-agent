import hashlib
import json

from django.core.cache import cache
from django.db.models import Count, Max, Q
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView


class IsAdminUserOrReadOnly(permissions.BasePermission):
    """Authenticated users can read; only admins can write."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return request.user and request.user.is_authenticated
        return bool(request.user and request.user.is_staff)

from .filters import JobFilter
from .models import EmployerCandidate, Job, JobSource, UserJob, UserPreference, UserProfile
from .discovery import probe_employer_name, seed_entry_for_result
from .discovery.approval import ApprovalError, approve_candidate, reject_candidate
from .discovery.suggest import SuggestionError, suggest_employers
from .discovery.verify import VerificationError, verify_candidate
from .services.scan import scan_sources
from .parsing.profile import ParseError, parse_cv, preview_redacted_text
from .parsing.redaction import redaction_counts
from .search.countries import detect_countries, nearby_suggestions
from .scan.scorer import _extract_required_years, get_or_build_scan_profile, score_job
from .search.ranker import build_intent_from_profile, rank_job

from .serializers import (
    EmployerCandidateSerializer,
    JobSerializer,
    JobSourceSerializer,
    ParsedProfileSerializer,
    UserJobSerializer,
    UserPreferenceSerializer,
    UserProfileSerializer,
)


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def health_check(request):
    return Response({'status': 'ok'})


class UserJobViewSet(viewsets.ModelViewSet):
    serializer_class = UserJobSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return UserJob.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class JobViewSet(viewsets.ModelViewSet):
    queryset = Job.objects.all().order_by('-date_discovered')
    serializer_class = JobSerializer
    filterset_class = JobFilter
    permission_classes = [IsAdminUserOrReadOnly]


class JobSourceViewSet(viewsets.ModelViewSet):
    queryset = JobSource.objects.all().order_by('name')
    serializer_class = JobSourceSerializer
    permission_classes = [IsAdminUserOrReadOnly]


class EmployerCandidateViewSet(viewsets.ModelViewSet):
    """User-specific employer suggestions discovered from their profile."""

    serializer_class = EmployerCandidateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return EmployerCandidate.objects.filter(user=self.request.user).order_by(
            '-created_at'
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['post'])
    def suggest(self, request):
        """Generate employer suggestions from the user's redacted profile."""
        profile = _get_or_create_profile(request.user)
        provider = getattr(request, '_llm_provider', None)
        force = request.data.get('force', False) is True
        try:
            candidates = suggest_employers(
                request.user,
                profile,
                provider=provider,
                force=force,
            )
        except SuggestionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        serializer = self.get_serializer(candidates, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        """Probe public ATS boards for a single candidate."""
        candidate = self.get_object()
        try:
            verify_candidate(candidate)
        except VerificationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        serializer = self.get_serializer(candidate)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a verified candidate and create a shared JobSource."""
        candidate = self.get_object()
        try:
            source = approve_candidate(candidate)
        except ApprovalError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'candidate': self.get_serializer(candidate).data,
            'source': JobSourceSerializer(source).data,
        })

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject a candidate."""
        candidate = self.get_object()
        reject_candidate(candidate)
        return Response(self.get_serializer(candidate).data)

    @action(detail=False, methods=['post'])
    def scan_now(self, request):
        """Run a bounded scan for sources approved by this user."""
        approved = EmployerCandidate.objects.filter(
            user=request.user,
            status=EmployerCandidate.Status.APPROVED,
            adapter_key__isnull=False,
            board_value__isnull=False,
        )
        names = [
            f"{c.adapter_key}:{c.board_value}"
            for c in approved
            if c.adapter_key and c.board_value
        ]
        sources = list(JobSource.objects.filter(name__in=names).order_by("name"))
        if not sources:
            return Response(
                {'detail': 'No approved sources to scan. Approve candidates first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_sources = int(request.data.get('max_sources', 10))
        max_pages = int(request.data.get('max_pages_per_source', 5))
        max_requests = int(request.data.get('max_requests', 50))
        min_interval = float(request.data.get('min_interval', 1.0))

        results = scan_sources(
            sources[:max_sources],
            max_pages=max_pages,
            max_requests=max_requests,
            min_interval=min_interval,
            mark_expired=True,
        )
        data = [
            {
                'source': JobSourceSerializer(r.source).data,
                'summary': {
                    'total_records': r.summary.total_records,
                    'created': r.summary.created,
                    'updated': r.summary.updated,
                    'skipped': r.summary.skipped,
                    'failed': r.summary.failed,
                    'salary_dropped': r.summary.salary_dropped,
                    'fetch_complete': r.summary.fetch_complete,
                    'errors': r.summary.errors,
                },
                'country_counts': r.country_counts,
            }
            for r in results
        ]
        return Response({'results': data})


class UserPreferenceView(APIView):
    def get_object(self):
        preference, _ = UserPreference.objects.get_or_create(user=self.request.user)
        return preference

    def get(self, request):
        serializer = UserPreferenceSerializer(self.get_object())
        return Response(serializer.data)

    def put(self, request):
        serializer = UserPreferenceSerializer(
            self.get_object(),
            data=request.data,
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request):
        serializer = UserPreferenceSerializer(
            self.get_object(),
            data=request.data,
            partial=True,
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


def _intent_has_ranking_data(intent) -> bool:
    """Return True if *intent* carries any signal that can rank jobs."""
    return bool(
        intent.job_titles
        or intent.skills
        or intent.locations
        or intent.seniority_levels
        or intent.employment_types
        or intent.work_arrangements
        or intent.industries
        or intent.salary_min is not None
    )


def _profile_hash(user) -> str:
    """Hash the profile inputs that affect ranking.

    Only non-default preference values are included so an auto-created empty
    UserPreference record does not change the key.
    """
    try:
        profile = UserProfile.objects.get(user=user)
        parsed = profile.parsed_profile
    except UserProfile.DoesNotExist:
        parsed = {}
    pref = UserPreference.objects.filter(user=user).first()
    prefs = {}
    if pref:
        if pref.job_titles:
            prefs['job_titles'] = pref.job_titles
        if pref.industries:
            prefs['industries'] = pref.industries
        if pref.locations:
            prefs['locations'] = pref.locations
        if pref.seniority_levels:
            prefs['seniority_levels'] = pref.seniority_levels
        if pref.employment_types:
            prefs['employment_types'] = pref.employment_types
        if pref.work_arrangements:
            prefs['work_arrangements'] = pref.work_arrangements
        if pref.min_salary is not None:
            prefs['min_salary'] = pref.min_salary
        if pref.salary_currency and pref.salary_currency != 'USD':
            prefs['salary_currency'] = pref.salary_currency
    data = {'parsed_profile': parsed, 'preferences': prefs}
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode()
    ).hexdigest()


def _jobs_data_version() -> str:
    """Return a short token that changes when jobs are created or updated."""
    agg = Job.objects.aggregate(max_updated=Max('updated_at'), count=Count('id'))
    max_updated = agg['max_updated']
    return f"{agg['count']}:{max_updated.isoformat() if max_updated else ''}"


def _filter_signature(query_params, country: str) -> str:
    """Hash the user-set hard filters (page number excluded)."""
    params = [(k, v) for k, v in query_params.items() if k != 'page']
    if country:
        params.append(('country', country))
    return hashlib.sha256(
        json.dumps(sorted(params), default=str).encode()
    ).hexdigest()[:32]


def _search_cache_key(user_id, profile_hash: str, filter_signature: str, data_version: str) -> str:
    return f"search_ranking:{user_id}:{profile_hash}:{filter_signature}:{data_version}"


def _job_matches_country(job, country: str, job_countries: set[str] | None = None) -> bool:
    """Portable country matching using the alias table in Python."""
    if not country:
        return True
    matched = job_countries if job_countries is not None else set(detect_countries(job.location or ''))
    if country in matched:
        return True
    if country == 'Remote' and job.work_arrangement == 'remote':
        return True
    return False


class SearchView(APIView):
    """Return all stored jobs ranked by the user's profile.

    Profile data affects scores and reasons only; it never hides jobs.
    Hard filters come only from query-string parameters.
    """

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        country = request.query_params.get("country", "").strip()

        profile_hash = _profile_hash(request.user)
        data_version = _jobs_data_version()
        filter_signature = _filter_signature(request.query_params, country)
        cache_key = _search_cache_key(
            request.user.id, profile_hash, filter_signature, data_version
        )
        cached = cache.get(cache_key)
        if cached is not None:
            results = cached["results"]
            verdict_counts = cached["verdict_counts"]
            paginator = PageNumberPagination()
            page = paginator.paginate_queryset(results, request)
            response = paginator.get_paginated_response(page)
            response.data["verdict_counts"] = verdict_counts
            return response

        intent = build_intent_from_profile(request.user)
        if query:
            intent.keywords = query

        # Start with every job; apply only explicit query-string filters.
        queryset = Job.objects.select_related("source").order_by("-date_discovered", "-id")
        filter_params = request.query_params.copy()
        filter_params.pop("country", None)
        if query:
            filter_params["search"] = query
        job_filter = JobFilter(filter_params, queryset=queryset)
        queryset = job_filter.qs

        has_ranking_data = _intent_has_ranking_data(intent)
        scan_profile = get_or_build_scan_profile(request.user) if has_ranking_data else {}

        results = []
        verdict_counts = {
            "apply": 0,
            "track": 0,
            "ignore": 0,
            "not_scored": 0,
        }
        for job in queryset:
            job_text = f"{job.title or ''} {job.company or ''} {job.description or ''} {job.location or ''}"
            job_countries = set(detect_countries(job.location))
            if country and not _job_matches_country(job, country, job_countries):
                continue
            serialized = JobSerializer(job).data
            if has_ranking_data:
                fit_rank = rank_job(job, intent, job_text=job_text, job_countries=job_countries)
                required_years = _extract_required_years(job)
                serialized.update(score_job(
                    job,
                    fit_rank,
                    scan_profile,
                    job_text=job_text,
                    job_countries=job_countries,
                    required_years=required_years,
                ))
            else:
                serialized.update({
                    "fit_score": None,
                    "access_score": None,
                    "level_score": None,
                    "total_score": None,
                    "verdict": "not_scored",
                    "matched_criteria": [],
                    "unmatched_criteria": [],
                    "unknown_criteria": [],
                    "access_reason": "",
                    "level_reason": "",
                    "gate_reasons": [],
                    "is_confirmed": False,
                    "is_lead": False,
                    "closing_soon": False,
                    "heuristic": True,
                    "not_scored_reason": "not scored: add preferences in Profile",
                })
            verdict_counts[serialized.get("verdict", "not_scored")] += 1
            results.append(serialized)

        if has_ranking_data:
            results.sort(
                key=lambda item: (item["total_score"], item.get("date_discovered") or ""),
                reverse=True,
            )

        cache.set(
            cache_key,
            {"results": results, "verdict_counts": verdict_counts},
            timeout=300,
        )

        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(results, request)
        response = paginator.get_paginated_response(page)
        response.data["verdict_counts"] = verdict_counts
        return response


class StatsSummaryView(APIView):
    """High-level counts for the dashboard."""

    def get(self, request):
        total = Job.objects.count()
        active = Job.objects.filter(
            source_listing_status=Job.SourceListingStatus.ACTIVE
        ).count()
        expired = Job.objects.filter(
            source_listing_status=Job.SourceListingStatus.EXPIRED
        ).count()
        # Saved and applied jobs are user-tracked states, not legacy job fields.
        saved = UserJob.objects.filter(user=request.user).count()
        applied = UserJob.objects.filter(
            user=request.user,
            application_status=Job.ApplicationStatus.APPLIED,
        ).count()
        sources = JobSource.objects.count()
        return Response({
            "total_jobs": total,
            "active_jobs": active,
            "expired_jobs": expired,
            "saved_jobs": saved,
            "applied_jobs": applied,
            "sources": sources,
        })


class FilterOptionsView(APIView):
    """Return available filter choices with record counts."""

    def get(self, request):
        def counts(field_name):
            return {
                item[field_name]: item["count"]
                for item in Job.objects.values(field_name)
                .annotate(count=Count("id"))
                .order_by("-count")
            }

        return Response({
            "seniority": counts("seniority"),
            "employment_type": counts("employment_type"),
            "work_arrangement": counts("work_arrangement"),
            "application_status": counts("application_status"),
            "source_listing_status": counts("source_listing_status"),
        })


class SourceStatusView(APIView):
    """Per-source ingestion health and job counts."""

    def get(self, request):
        sources = JobSource.objects.annotate(
            job_count=Count("jobs"),
            active_count=Count(
                "jobs",
                filter=Q(jobs__source_listing_status=Job.SourceListingStatus.ACTIVE),
            ),
            expired_count=Count(
                "jobs",
                filter=Q(jobs__source_listing_status=Job.SourceListingStatus.EXPIRED),
            ),
        ).order_by("name")
        data = [
            {
                "id": source.id,
                "name": source.name,
                "is_active": source.is_active,
                "last_ingested_at": source.last_ingested_at,
                "last_ingestion_status": source.last_ingestion_status,
                "last_ingestion_error": source.last_ingestion_error,
                "job_count": source.job_count,
                "active_job_count": source.active_count,
                "expired_job_count": source.expired_count,
            }
            for source in sources
        ]
        return Response(data)


def _get_or_create_profile(user) -> UserProfile:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


class UserProfileView(APIView):
    """Get, update, or delete the authenticated user's CV/profile."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profile = _get_or_create_profile(request.user)
        serializer = UserProfileSerializer(profile)
        return Response(serializer.data)

    def put(self, request):
        profile = _get_or_create_profile(request.user)
        serializer = UserProfileSerializer(
            profile, data=request.data, partial=False
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request):
        profile = _get_or_create_profile(request.user)
        serializer = UserProfileSerializer(
            profile, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request):
        profile = _get_or_create_profile(request.user)
        profile.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UploadCVView(APIView):
    """Upload or replace the user's CV file."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if 'cv' not in request.FILES:
            return Response(
                {'cv': 'No file provided'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile = _get_or_create_profile(request.user)
        if profile.cv:
            profile.delete_cv_file()
            profile.save(update_fields=['cv'])
        serializer = UserProfileSerializer(
            profile,
            data={'cv': request.FILES['cv']},
            partial=True,
        )
        if serializer.is_valid():
            serializer.save()
            return Response(UserProfileSerializer(profile).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class DeleteCVView(APIView):
    """Delete only the CV file, keeping the profile and parsed data."""

    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request):
        profile = _get_or_create_profile(request.user)
        profile.delete_cv_file()
        profile.cv_parsing_consent = False
        profile.cv_parsing_consent_at = None
        profile.save(update_fields=['cv', 'cv_parsing_consent', 'cv_parsing_consent_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PreviewRedactedView(APIView):
    """Return the redacted CV text without calling the LLM."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        profile = _get_or_create_profile(request.user)
        if not profile.cv:
            return Response(
                {'detail': 'No CV uploaded'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            text = preview_redacted_text(profile)
        except ParseError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'redacted_text': text,
            'counts': redaction_counts(text),
        })


class ParseCVView(APIView):
    """Parse the uploaded CV into an editable profile."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        profile = _get_or_create_profile(request.user)
        # Tests inject the fake provider via a custom attribute on the request.
        provider = getattr(request, '_llm_provider', None)
        try:
            parse_cv(profile, provider=provider)
        except ParseError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        serializer = UserProfileSerializer(profile)
        return Response(serializer.data)


class DiscoveryProbeView(APIView):
    """Probe public ATS boards for a company name and store inactive candidates."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        name = str(request.data.get("name", "")).strip()
        if not name:
            return Response({"detail": "name is required"}, status=status.HTTP_400_BAD_REQUEST)

        outcomes = probe_employer_name(name)
        candidates = []
        for outcome in outcomes:
            item = {
                "adapter": outcome.adapter_key,
                "board_value": outcome.board_value,
                "status": outcome.status,
                "detail": outcome.detail,
            }
            if outcome.http_status:
                item["http_status"] = outcome.http_status
            if outcome.result:
                item["job_count"] = outcome.result.job_count
                item["seed_entry"] = seed_entry_for_result(name, outcome.result)
            candidates.append(item)
        return Response({"company_name": name, "candidates": candidates})


class CoverageView(APIView):
    """Return per-country job counts and honest coverage suggestions."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from core.search.countries import detect_country

        intent = build_intent_from_profile(request.user)
        requested_countries = set()
        for loc in intent.locations:
            country = detect_country(loc)
            if country:
                requested_countries.add(country)
            else:
                requested_countries.add(loc)

        country_counts = {}
        for location in (
            Job.objects.filter(source_listing_status=Job.SourceListingStatus.ACTIVE)
            .values_list("location", flat=True)
            .distinct()
        ):
            countries = detect_countries(location)
            job_count = Job.objects.filter(
                location=location, source_listing_status=Job.SourceListingStatus.ACTIVE
            ).count()
            if countries:
                for country in countries:
                    country_counts[country] = country_counts.get(country, 0) + job_count
            else:
                key = location or "Unknown"
                country_counts[key] = country_counts.get(key, 0) + job_count

        suggestions = {}
        low_threshold = 3
        for country in requested_countries:
            count = country_counts.get(country, 0)
            if count < low_threshold:
                nearby = nearby_suggestions(country)
                suggestion = f"Only {count} active role{'s' if count != 1 else ''} in {country}."
                if nearby:
                    suggestion += (
                        f" Consider including remote roles or nearby countries: "
                        f"{', '.join(nearby)}."
                    )
                else:
                    suggestion += " Consider including remote roles."
                suggestions[country] = suggestion

        return Response({"country_counts": country_counts, "suggestions": suggestions})
