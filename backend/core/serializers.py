from django.conf import settings
from rest_framework import serializers

from .models import EmployerCandidate, Job, JobSource, UserJob, UserPreference, UserProfile
from .utils.secrets import scan_for_secrets


class JobSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobSource
        fields = [
            'id',
            'name',
            'base_url',
            'is_active',
            'source_type',
            'access_method',
            'refresh_frequency',
            'config',
            'last_ingested_at',
            'last_ingestion_status',
            'last_ingestion_error',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'last_ingested_at',
            'last_ingestion_status',
            'last_ingestion_error',
            'created_at',
            'updated_at',
        ]

    def validate_config(self, value):
        if value is None:
            value = {}
        offending = scan_for_secrets(value)
        if offending:
            raise serializers.ValidationError(
                f"Config key(s) {offending} are not allowed. Store secrets in environment variables."
            )
        return value


class JobSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = [
            'id',
            'title',
            'company',
            'description',
            'location',
            'industry',
            'source',
            'source_url',
            'external_id',
            'employment_type',
            'work_arrangement',
            'seniority',
            'salary_min',
            'salary_max',
            'salary_currency',
            'salary_period',
            'date_posted',
            'date_discovered',
            'application_deadline',
            'application_status',
            'source_listing_status',
            'duplicate_hash',
            'raw_data',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['date_discovered', 'duplicate_hash', 'created_at', 'updated_at']

    def validate_external_id(self, value):
        if value is None:
            return None
        stripped = value.strip()
        return stripped if stripped else None

    def validate(self, data):
        salary_min = data.get('salary_min')
        salary_max = data.get('salary_max')

        if salary_min is not None and salary_max is not None and salary_min > salary_max:
            raise serializers.ValidationError(
                {'salary_min': 'salary_min must be less than or equal to salary_max.'}
            )

        source = data.get('source')
        external_id = data.get('external_id')

        if source is not None and external_id:
            queryset = Job.objects.filter(source=source, external_id=external_id)
            if self.instance is not None:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError(
                    {'external_id': 'A job with this source and external ID already exists.'}
                )

        return data


class AccessRowSerializer(serializers.Serializer):
    country = serializers.CharField(max_length=100)
    score = serializers.IntegerField(min_value=1, max_value=5)
    reason = serializers.CharField(max_length=255, allow_blank=True, required=False)
    last_reviewed = serializers.DateTimeField(allow_null=True, required=False)


class GatesSerializer(serializers.Serializer):
    exclude_countries = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=list
    )
    exclude_role_types = serializers.ListField(
        child=serializers.CharField(max_length=50), required=False, default=list
    )
    exclude_unpaid = serializers.BooleanField(required=False, default=True)
    max_years_required = serializers.IntegerField(
        min_value=0, allow_null=True, required=False
    )
    clearance_phrases = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=list
    )
    missing_certifications = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=list
    )


class ThresholdsSerializer(serializers.Serializer):
    apply = serializers.IntegerField(min_value=0, max_value=125)
    track = serializers.IntegerField(min_value=0, max_value=125)
    ignore = serializers.IntegerField(min_value=0, max_value=125)


class ScanProfileSerializer(serializers.Serializer):
    home_country = serializers.CharField(
        max_length=100, allow_blank=True, required=False
    )
    relocation_countries = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=list
    )
    remote_enabled = serializers.BooleanField(required=False, default=True)
    years_of_experience = serializers.IntegerField(
        min_value=0, allow_null=True, required=False
    )
    access_table = serializers.ListField(
        child=AccessRowSerializer(), required=False, default=list
    )
    gates = GatesSerializer(required=False, default=dict)
    thresholds = ThresholdsSerializer(required=False, default=dict)
    role_families = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=list
    )


class UserPreferenceSerializer(serializers.ModelSerializer):
    scan_profile = serializers.JSONField(required=False)

    class Meta:
        model = UserPreference
        fields = [
            'id',
            'user',
            'job_titles',
            'industries',
            'locations',
            'seniority_levels',
            'employment_types',
            'work_arrangements',
            'scan_profile',
            'min_salary',
            'salary_currency',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['user', 'created_at', 'updated_at']

    def validate_scan_profile(self, value):
        if value is None:
            value = {}
        serializer = ScanProfileSerializer(data=value)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class UserJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserJob
        fields = [
            'id',
            'user',
            'job',
            'application_status',
            'notes',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['user', 'created_at', 'updated_at']

    def validate(self, data):
        request = self.context.get('request')
        user = request.user if request else None
        job = data.get('job')
        if (
            user is not None
            and job is not None
            and UserJob.objects.filter(user=user, job=job)
            .exclude(pk=self.instance.pk if self.instance else None)
            .exists()
        ):
            raise serializers.ValidationError(
                {'job': 'You have already tracked this job.'}
            )
        return data


class ExperienceSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, allow_blank=True, required=False)
    company = serializers.CharField(max_length=255, allow_blank=True, required=False)
    location = serializers.CharField(max_length=255, allow_blank=True, required=False)
    start_date = serializers.CharField(max_length=20, allow_blank=True, required=False)
    end_date = serializers.CharField(max_length=20, allow_blank=True, required=False)
    description = serializers.CharField(
        allow_blank=True, required=False, max_length=2000
    )


class EducationSerializer(serializers.Serializer):
    institution = serializers.CharField(
        max_length=255, allow_blank=True, required=False
    )
    degree = serializers.CharField(max_length=255, allow_blank=True, required=False)
    field = serializers.CharField(max_length=255, allow_blank=True, required=False)
    start_date = serializers.CharField(max_length=20, allow_blank=True, required=False)
    end_date = serializers.CharField(max_length=20, allow_blank=True, required=False)


class PreferencesSerializer(serializers.Serializer):
    job_titles = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=list
    )
    industries = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=list
    )
    locations = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=list
    )
    employment_types = serializers.ListField(
        child=serializers.CharField(max_length=50), required=False, default=list
    )
    work_arrangements = serializers.ListField(
        child=serializers.CharField(max_length=50), required=False, default=list
    )
    seniority_levels = serializers.ListField(
        child=serializers.CharField(max_length=50), required=False, default=list
    )
    min_salary = serializers.IntegerField(
        required=False, allow_null=True, min_value=0
    )


class ParsedProfileSerializer(serializers.Serializer):
    """Strict, simple schema for LLM output and user edits."""

    headline = serializers.CharField(
        max_length=255, allow_blank=True, required=False
    )
    summary = serializers.CharField(
        allow_blank=True, required=False, max_length=5000
    )
    location = serializers.CharField(
        max_length=255, allow_blank=True, required=False
    )
    skills = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=list
    )
    experience = serializers.ListField(
        child=ExperienceSerializer(), required=False, default=list
    )
    education = serializers.ListField(
        child=EducationSerializer(), required=False, default=list
    )
    preferences = PreferencesSerializer(required=False, default=dict)


class EmployerCandidateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployerCandidate
        fields = [
            'id',
            'display_name',
            'normalized_name',
            'status',
            'reason',
            'adapter_key',
            'board_value',
            'sample_jobs',
            'last_probed_at',
            'probe_count',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'normalized_name',
            'status',
            'reason',
            'adapter_key',
            'board_value',
            'sample_jobs',
            'last_probed_at',
            'probe_count',
            'created_at',
            'updated_at',
        ]


class UserProfileSerializer(serializers.ModelSerializer):
    parsed_profile = ParsedProfileSerializer(required=False)

    class Meta:
        model = UserProfile
        fields = [
            'id',
            'user',
            'cv',
            'cv_parsing_consent',
            'cv_parsing_consent_at',
            'parsed_profile',
            'redacted_text',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'user',
            'cv_parsing_consent_at',
            'redacted_text',
            'created_at',
            'updated_at',
        ]

    def validate_cv(self, value):
        if value is None:
            return value
        max_size = getattr(settings, 'CV_MAX_FILE_SIZE_MB', 5) * 1024 * 1024
        if value.size > max_size:
            raise serializers.ValidationError(
                f"File exceeds {max_size // (1024 * 1024)} MB"
            )
        ext = value.name.split('.')[-1].lower()
        if ext not in {'pdf', 'docx', 'txt'}:
            raise serializers.ValidationError(
                "Only PDF, DOCX and TXT files are allowed"
            )
        return value

    def update(self, instance, validated_data):
        consent = validated_data.get('cv_parsing_consent')
        if consent is not None and consent != instance.cv_parsing_consent:
            from django.utils import timezone

            instance.cv_parsing_consent_at = timezone.now()
        parsed = validated_data.pop('parsed_profile', None)
        if parsed is not None:
            serializer = ParsedProfileSerializer(data=parsed)
            serializer.is_valid(raise_exception=True)
            instance.parsed_profile = serializer.validated_data
        return super().update(instance, validated_data)
