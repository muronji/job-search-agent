import os
import uuid

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import models, IntegrityError


def _cv_upload_path(instance, filename):
    """Return a private, random file path for a CV upload."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in {'.pdf', '.docx', '.txt'}:
        ext = '.bin'
    return f"private/cv/{uuid.uuid4()}{ext}"


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class JobSource(TimestampedModel):
    class SourceType(models.TextChoices):
        JOB_BOARD = 'job_board', 'Job board'
        ATS = 'ats', 'ATS'
        GOVERNMENT = 'government', 'Government'
        FEED = 'feed', 'Feed'
        FILE = 'file', 'File'
        SEARCH = 'search', 'Search'
        OTHER = 'other', 'Other'

    class AccessMethod(models.TextChoices):
        API = 'api', 'API'
        RSS = 'rss', 'RSS'
        FILE = 'file', 'File'
        MANUAL = 'manual', 'Manual'

    class RefreshFrequency(models.TextChoices):
        MANUAL = 'manual', 'Manual'
        HOURLY = 'hourly', 'Hourly'
        DAILY = 'daily', 'Daily'
        WEEKLY = 'weekly', 'Weekly'

    class IngestionStatus(models.TextChoices):
        UNKNOWN = 'unknown', 'Unknown'
        SUCCESS = 'success', 'Success'
        PARTIAL = 'partial', 'Partial'
        FAILURE = 'failure', 'Failure'

    name = models.CharField(max_length=100, unique=True)
    base_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    source_type = models.CharField(
        max_length=50,
        choices=SourceType.choices,
        default=SourceType.OTHER,
    )
    access_method = models.CharField(
        max_length=50,
        choices=AccessMethod.choices,
        default=AccessMethod.MANUAL,
    )
    refresh_frequency = models.CharField(
        max_length=50,
        choices=RefreshFrequency.choices,
        default=RefreshFrequency.MANUAL,
    )
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text='Public, non-secret source-specific configuration.',
    )

    last_ingested_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp of the most recent successful ingestion run.',
    )
    last_ingestion_status = models.CharField(
        max_length=50,
        choices=IngestionStatus.choices,
        default=IngestionStatus.UNKNOWN,
    )
    last_ingestion_error = models.TextField(
        blank=True,
        help_text='Short summary of the most recent ingestion failure.',
    )

    def __str__(self):
        return self.name


class Job(TimestampedModel):
    class EmploymentType(models.TextChoices):
        FULL_TIME = 'full_time', 'Full-time'
        PART_TIME = 'part_time', 'Part-time'
        CONTRACT = 'contract', 'Contract'
        INTERNSHIP = 'internship', 'Internship'
        TEMPORARY = 'temporary', 'Temporary'
        FREELANCE = 'freelance', 'Freelance'
        VOLUNTEER = 'volunteer', 'Volunteer'
        OTHER = 'other', 'Other'

    class WorkArrangement(models.TextChoices):
        ONSITE = 'onsite', 'On-site'
        REMOTE = 'remote', 'Remote'
        HYBRID = 'hybrid', 'Hybrid'

    class Seniority(models.TextChoices):
        ENTRY = 'entry', 'Entry-level'
        JUNIOR = 'junior', 'Junior'
        MID = 'mid', 'Mid-level'
        SENIOR = 'senior', 'Senior'
        LEAD = 'lead', 'Lead'
        MANAGER = 'manager', 'Manager'
        EXECUTIVE = 'executive', 'Executive'
        OTHER = 'other', 'Other'

    class ApplicationStatus(models.TextChoices):
        SAVED = 'saved', 'Saved'
        APPLIED = 'applied', 'Applied'
        INTERVIEWING = 'interviewing', 'Interviewing'
        OFFERED = 'offered', 'Offered'
        REJECTED = 'rejected', 'Rejected'
        WITHDRAWN = 'withdrawn', 'Withdrawn'
        CLOSED = 'closed', 'Closed'
        HIRED = 'hired', 'Hired'

    class SalaryPeriod(models.TextChoices):
        HOURLY = 'hourly', 'Hourly'
        MONTHLY = 'monthly', 'Monthly'
        YEARLY = 'yearly', 'Yearly'

    class SourceListingStatus(models.TextChoices):
        ACTIVE = 'active', 'Active'
        EXPIRED = 'expired', 'Expired'
        UNKNOWN = 'unknown', 'Unknown'

    title = models.CharField(max_length=255)
    company = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    industry = models.CharField(max_length=100, blank=True)

    source = models.ForeignKey(
        JobSource,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='jobs',
    )
    source_url = models.URLField(blank=True)
    external_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        default=None,
    )

    employment_type = models.CharField(
        max_length=50,
        choices=EmploymentType.choices,
        blank=True,
    )
    work_arrangement = models.CharField(
        max_length=50,
        choices=WorkArrangement.choices,
        blank=True,
    )
    seniority = models.CharField(
        max_length=50,
        choices=Seniority.choices,
        blank=True,
    )

    salary_min = models.PositiveIntegerField(null=True, blank=True)
    salary_max = models.PositiveIntegerField(null=True, blank=True)
    salary_currency = models.CharField(
        max_length=3,
        default='USD',
        blank=True,
    )
    salary_period = models.CharField(
        max_length=20,
        choices=SalaryPeriod.choices,
        blank=True,
    )

    date_posted = models.DateField(null=True, blank=True)
    date_discovered = models.DateTimeField(auto_now_add=True)
    application_deadline = models.DateField(null=True, blank=True)
    application_status = models.CharField(
        max_length=50,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.SAVED,
    )
    source_listing_status = models.CharField(
        max_length=50,
        choices=SourceListingStatus.choices,
        default=SourceListingStatus.ACTIVE,
    )
    duplicate_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        help_text='Advisory cross-source duplicate hash for manual review.',
    )

    raw_data = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'external_id'],
                condition=models.Q(
                    source__isnull=False,
                    external_id__isnull=False,
                ),
                name='unique_job_source_external_id_when_set',
            ),
        ]

    def __str__(self):
        return f'{self.title} at {self.company}'


class UserPreference(TimestampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='job_preferences',
    )

    job_titles = models.JSONField(default=list, blank=True)
    industries = models.JSONField(default=list, blank=True)
    locations = models.JSONField(default=list, blank=True)
    seniority_levels = models.JSONField(default=list, blank=True)
    employment_types = models.JSONField(default=list, blank=True)
    work_arrangements = models.JSONField(default=list, blank=True)

    scan_profile = models.JSONField(
        default=dict,
        blank=True,
        help_text='Scan-agent settings: access table, gates, thresholds, role families.',
    )

    min_salary = models.PositiveIntegerField(null=True, blank=True)
    salary_currency = models.CharField(max_length=3, default='USD')

    def __str__(self):
        if self.user:
            return f'Preferences for {self.user}'
        return 'Default preferences'


class UserJob(TimestampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='user_jobs',
    )
    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name='user_jobs',
    )
    application_status = models.CharField(
        max_length=50,
        choices=Job.ApplicationStatus.choices,
        default=Job.ApplicationStatus.SAVED,
    )
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'job'],
                name='unique_user_job',
            ),
        ]

    def __str__(self):
        return f'{self.user} - {self.job}'


class UserProfile(TimestampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='cv_profile',
    )
    cv = models.FileField(
        upload_to=_cv_upload_path,
        blank=True,
        null=True,
        max_length=255,
        help_text='Uploaded CV file (PDF, DOCX, or TXT). Stored outside any public path.',
    )
    cv_parsing_consent = models.BooleanField(default=False)
    cv_parsing_consent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the user last gave parsing consent.',
    )
    parsed_profile = models.JSONField(
        default=dict,
        blank=True,
        help_text='Editable structured profile extracted from the CV.',
    )
    redacted_text = models.TextField(
        blank=True,
        help_text='Redacted CV text used for LLM calls and preview (no PII).',
    )

    def delete_cv_file(self):
        if self.cv:
            default_storage.delete(self.cv.name)
            self.cv = None

    def __str__(self):
        return f'Profile for {self.user}'


from django.db.models.signals import post_delete
from django.dispatch import receiver


@receiver(post_delete, sender=UserProfile)
def _delete_cv_file_on_profile_delete(sender, instance, **kwargs):
    """Ensure the CV file is removed when the profile is deleted by any path."""
    instance.delete_cv_file()


class EmployerCandidate(TimestampedModel):
    class Status(models.TextChoices):
        SUGGESTED = 'suggested', 'Suggested'
        NO_SUPPORTED_BOARD = 'no_supported_board', 'No supported board'
        FOUND = 'found', 'Found'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='employer_candidates',
    )
    display_name = models.CharField(max_length=100)
    normalized_name = models.CharField(max_length=100, db_index=True)
    status = models.CharField(
        max_length=50,
        choices=Status.choices,
        default=Status.SUGGESTED,
    )
    reason = models.TextField(
        blank=True,
        help_text='Plain-text reason from the LLM, length-capped.',
    )
    adapter_key = models.CharField(
        max_length=50,
        blank=True,
        help_text='ATS adapter that found a board for this candidate, if any.',
    )
    board_value = models.CharField(
        max_length=100,
        blank=True,
        help_text='Sanitized board token, if a board was found.',
    )
    sample_jobs = models.JSONField(
        default=list,
        blank=True,
        help_text='Sample job titles and locations from verification.',
    )
    last_probed_at = models.DateTimeField(null=True, blank=True)
    probe_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'normalized_name'],
                name='unique_employer_candidate_per_user',
            ),
        ]

    def __str__(self):
        return f'{self.display_name} ({self.status})'


class LLMUsageLog(TimestampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='llm_usage_logs',
    )
    model = models.CharField(max_length=50)
    input_tokens = models.PositiveIntegerField()
    output_tokens = models.PositiveIntegerField()
    estimated_cost_usd = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=0,
    )

    def __str__(self):
        return f'{self.user} - {self.model} - {self.input_tokens}/{self.output_tokens} tokens'
