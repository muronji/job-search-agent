"""Core ingestion service for importing jobs from adapters."""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable

from core.adapters.base import BaseAdapter
from core.models import Job, JobSource
from core.serializers import JobSerializer


@dataclass
class IngestionSummary:
    total_records: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    salary_dropped: int = 0
    fetch_complete: bool = False
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str):
        self.errors.append(message)
        self.failed += 1

    def validate_counts(self):
        return (
            self.total_records
            == self.created + self.updated + self.skipped + self.failed
        )


def _compute_duplicate_hash(title: str, company: str, location: str) -> str:
    payload = "|".join([
        title.strip().lower(),
        company.strip().lower(),
        location.strip().lower(),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _merge_salary(existing: Job, incoming: dict) -> tuple[int | None, int | None]:
    min_val = incoming.get("salary_min") if incoming.get("salary_min") is not None else existing.salary_min
    max_val = incoming.get("salary_max") if incoming.get("salary_max") is not None else existing.salary_max
    return min_val, max_val


def _safe_raw_data(raw: dict) -> dict:
    """Return a sanitized, size-limited copy of raw source data."""
    cleaned = {k: v for k, v in raw.items() if k not in {"_source_name"}}
    try:
        serialized = json.dumps(cleaned)
    except (TypeError, ValueError):
        return {}
    if len(serialized.encode("utf-8")) > 100 * 1024:
        return {}
    return cleaned


def _apply_update(existing: Job, incoming: dict) -> tuple[bool, bool]:
    """Apply safe update semantics to an existing job.

    Returns (changed, salary_dropped).
    """
    changed = False

    # User-managed fields are never overwritten.
    source_managed = {
        "title", "company", "description", "location", "industry",
        "source_url", "employment_type", "work_arrangement", "seniority",
        "salary_currency", "salary_period", "date_posted", "application_deadline",
    }

    for field_name in source_managed:
        new_value = incoming.get(field_name)
        if new_value in (None, ""):
            continue
        if field_name in {"employment_type", "work_arrangement", "seniority", "salary_period"}:
            if new_value not in dict(Job._meta.get_field(field_name).choices):
                continue
        current = getattr(existing, field_name)
        if new_value != current:
            setattr(existing, field_name, new_value)
            changed = True

    # Salary pair is validated as a merged unit; drop on inconsistency.
    salary_dropped = False
    merged_min, merged_max = _merge_salary(existing, incoming)
    if merged_min is not None and merged_max is not None and merged_min > merged_max:
        merged_min, merged_max = None, None
        salary_dropped = True
    if merged_min != existing.salary_min:
        existing.salary_min = merged_min
        changed = True
    if merged_max != existing.salary_max:
        existing.salary_max = merged_max
        changed = True
    if salary_dropped:
        # Ensure currency/period are also cleared when the pair is dropped.
        if existing.salary_currency:
            existing.salary_currency = ""
            changed = True
        if existing.salary_period:
            existing.salary_period = ""
            changed = True

    # Always update raw_data and source listing status from ingestion.
    raw = _safe_raw_data(incoming.get("raw_data", {}))
    if existing.raw_data != raw:
        existing.raw_data = raw
        changed = True

    new_listing_status = incoming.get("source_listing_status")
    if new_listing_status and new_listing_status != existing.source_listing_status:
        existing.source_listing_status = new_listing_status
        changed = True

    return changed, salary_dropped


class IngestionService:
    """Run an adapter and persist its records safely."""

    def __init__(self, source: JobSource, adapter: BaseAdapter):
        self.source = source
        self.adapter = adapter

    def run(self, mark_expired: bool = False) -> IngestionSummary:
        summary = IngestionSummary()
        seen_external_ids: set[str | None] = set()

        try:
            raw_records: Iterable[dict] = self.adapter.fetch()
        except Exception as e:
            summary.add_error(f"Fetch failed: {e}")
            return summary

        for raw in raw_records:
            summary.total_records += 1
            try:
                normalized = self.adapter.normalize(raw)
                self._validate_source_name(normalized)
                data = self._prepare_data(normalized)
                if self._salary_pair_is_invalid(data):
                    data = self._drop_salary(data)
                    summary.salary_dropped += 1
                job, is_new, update_dropped = self._persist(data, seen_external_ids)
                if update_dropped:
                    summary.salary_dropped += 1
                if job is None:
                    summary.skipped += 1
                elif is_new:
                    summary.created += 1
                else:
                    summary.updated += 1
            except Exception as e:
                summary.add_error(f"Record failed: {e}")

        summary.fetch_complete = self.adapter.fetch_complete

        if (
            mark_expired
            and summary.fetch_complete
            and summary.failed == 0
            and summary.total_records > 0
        ):
            self._mark_expired(seen_external_ids)

        return summary

    def _validate_source_name(self, normalized: dict):
        incoming_name = normalized.get("_source_name", "")
        if incoming_name != self.source.name:
            raise ValueError(
                f"Source name mismatch: expected '{self.source.name}', got '{incoming_name}'"
            )

    def _prepare_data(self, normalized: dict) -> dict:
        data = {
            "title": normalized.get("title"),
            "company": normalized.get("company"),
            "description": normalized.get("description"),
            "location": normalized.get("location"),
            "industry": normalized.get("industry"),
            "source": self.source.pk,
            "source_url": normalized.get("source_url"),
            "external_id": normalized.get("external_id"),
            "employment_type": normalized.get("employment_type"),
            "work_arrangement": normalized.get("work_arrangement"),
            "seniority": normalized.get("seniority"),
            "salary_min": normalized.get("salary_min"),
            "salary_max": normalized.get("salary_max"),
            "salary_currency": normalized.get("salary_currency"),
            "salary_period": normalized.get("salary_period"),
            "date_posted": normalized.get("date_posted"),
            "application_deadline": normalized.get("application_deadline"),
            "application_status": Job.ApplicationStatus.SAVED,
            "source_listing_status": Job.SourceListingStatus.ACTIVE,
            "raw_data": _safe_raw_data(normalized.get("raw_data", {})),
        }
        return data

    @staticmethod
    def _salary_pair_is_invalid(data: dict) -> bool:
        salary_min = data.get("salary_min")
        salary_max = data.get("salary_max")
        return (
            salary_min is not None
            and salary_max is not None
            and salary_min > salary_max
        )

    @staticmethod
    def _drop_salary(data: dict) -> dict:
        data = dict(data)
        data["salary_min"] = None
        data["salary_max"] = None
        data["salary_currency"] = ""
        data["salary_period"] = ""
        return data

    def _persist(self, data: dict, seen_external_ids: set):
        external_id = data.get("external_id")
        seen_external_ids.add(external_id)

        if external_id:
            try:
                existing = Job.objects.get(source=self.source, external_id=external_id)
            except Job.DoesNotExist:
                existing = None
        else:
            existing = None

        if existing is None:
            serializer = JobSerializer(data=data)
            serializer.is_valid(raise_exception=True)
            job = serializer.save()
            is_new = True
            salary_dropped = False
        else:
            job = existing
            changed, salary_dropped = _apply_update(job, data)
            if not changed:
                return None, False, False
            job.save()
            is_new = False

        self._set_duplicate_hash(job)
        return job, is_new, salary_dropped

    def _set_duplicate_hash(self, job: Job):
        new_hash = _compute_duplicate_hash(job.title, job.company, job.location)
        if job.duplicate_hash != new_hash:
            job.duplicate_hash = new_hash
            job.save(update_fields=["duplicate_hash"])

    def _mark_expired(self, seen_external_ids: set):
        expired = Job.SourceListingStatus.EXPIRED
        active = Job.SourceListingStatus.ACTIVE
        Job.objects.filter(
            source=self.source,
            source_listing_status=active,
        ).exclude(
            external_id__in={eid for eid in seen_external_ids if eid},
        ).update(source_listing_status=expired)
