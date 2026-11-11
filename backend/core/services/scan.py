"""Shared scan logic used by the management command and the scan-now API."""
from collections import defaultdict
from dataclasses import dataclass, field

from django.utils import timezone

from core.adapters.registry import AdapterNotRegistered, get_adapter_class
from core.models import Job, JobSource
from core.search.countries import detect_countries
from core.services.ingestion import IngestionService
from core.utils.http import BudgetExhaustedError, DomainRateLimiter


class BudgetLimiter(DomainRateLimiter):
    """Rate limiter that also enforces a hard cap on total requests."""

    def __init__(self, max_requests: int, min_interval: float = 1.0):
        super().__init__(min_interval=min_interval)
        self.max_requests = max_requests
        self.request_count = 0

    def check(self, domain: str):
        if self.request_count >= self.max_requests:
            raise BudgetExhaustedError(
                f"Scan request budget exhausted ({self.max_requests})"
            )
        self.request_count += 1
        super().check(domain)


@dataclass
class ScanResult:
    source: JobSource
    summary: object
    country_counts: dict = field(default_factory=dict)
    unmapped: list = field(default_factory=list)


def _update_source_health(source: JobSource, summary):
    if summary.total_records == 0 and summary.failed == 0 and not summary.fetch_complete:
        source.last_ingestion_status = JobSource.IngestionStatus.FAILURE
        source.last_ingestion_error = "Fetch did not complete and no records were processed"
        source.save(update_fields=["last_ingestion_status", "last_ingestion_error"])
        return

    if summary.failed == 0:
        source.last_ingestion_status = JobSource.IngestionStatus.SUCCESS
    else:
        source.last_ingestion_status = JobSource.IngestionStatus.PARTIAL
    source.last_ingested_at = timezone.now()
    source.last_ingestion_error = ""
    if summary.errors:
        source.last_ingestion_error = "; ".join(summary.errors[:3])
    source.save(
        update_fields=[
            "last_ingested_at",
            "last_ingestion_status",
            "last_ingestion_error",
        ]
    )


def _country_counts_for_source(source: JobSource):
    counts = defaultdict(int)
    unmapped: list[str] = []
    for location in (
        Job.objects.filter(
            source=source, source_listing_status=Job.SourceListingStatus.ACTIVE
        )
        .values_list("location", flat=True)
        .distinct()
    ):
        countries = detect_countries(location)
        job_count = Job.objects.filter(
            source=source,
            location=location,
            source_listing_status=Job.SourceListingStatus.ACTIVE,
        ).count()
        if countries:
            for country in countries:
                counts[country] += job_count
        else:
            counts["Other"] += job_count
            unmapped.append(location)
    return dict(counts), unmapped


def scan_sources(
    sources,
    *,
    max_pages: int = 5,
    max_requests: int = 50,
    min_interval: float = 1.0,
    mark_expired: bool = True,
):
    """Scan a list of JobSource objects and return ScanResult objects."""
    import core.utils.http as http_module

    original_limiter = http_module._default_rate_limiter
    limiter = BudgetLimiter(max_requests=max_requests, min_interval=min_interval)
    http_module._default_rate_limiter = limiter

    try:
        results = []
        for source in sources:
            adapter_key = source.config.get("adapter_key")
            if not adapter_key:
                continue
            try:
                adapter_class = get_adapter_class(adapter_key)
            except AdapterNotRegistered:
                continue

            adapter_options = {"max_pages": max_pages, "wait": True}
            adapter = adapter_class(source, adapter_options)
            service = IngestionService(source, adapter)
            try:
                summary = service.run(mark_expired=mark_expired)
            except BudgetExhaustedError:
                break

            _update_source_health(source, summary)
            country_counts, unmapped = _country_counts_for_source(source)
            results.append(
                ScanResult(
                    source=source,
                    summary=summary,
                    country_counts=country_counts,
                    unmapped=unmapped,
                )
            )
        return results
    finally:
        http_module._default_rate_limiter = original_limiter
