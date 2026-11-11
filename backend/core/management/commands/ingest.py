"""Management command to ingest jobs from a configured source."""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.adapters.base import AdapterConfigError
from core.adapters.registry import AdapterNotRegistered, get_adapter_class
from core.models import JobSource
from core.services.ingestion import IngestionService


class Command(BaseCommand):
    help = "Ingest job listings from a configured source."

    def add_arguments(self, parser):
        parser.add_argument("--adapter", required=True, help="Adapter key (e.g. csv, json, usajobs)")
        parser.add_argument("--source-id", type=int, help="Existing JobSource ID")
        parser.add_argument("--source-name", help="Existing JobSource name")
        parser.add_argument("--create-source", action="store_true", help="Create the source if it does not exist")
        parser.add_argument("--file", help="Path to input file (CSV/JSON)")
        parser.add_argument("--feed-url", help="URL of RSS/Atom feed")
        parser.add_argument("--keywords", help="Search keywords (USAJobs)")
        parser.add_argument("--location", help="Location filter (USAJobs)")
        parser.add_argument("--mark-expired", action="store_true", help="Mark unseen source listings as expired")

    def handle(self, *args, **options):
        adapter_key = options["adapter"]
        try:
            adapter_class = get_adapter_class(adapter_key)
        except AdapterNotRegistered as e:
            raise CommandError(str(e))

        source = self._resolve_source(options)
        if not adapter_class.is_config_valid(source.config):
            raise CommandError(f"Invalid config for adapter '{adapter_key}'")

        adapter_options = {
            "file": options.get("file"),
            "feed_url": options.get("feed_url"),
            "keywords": options.get("keywords"),
            "location": options.get("location"),
        }
        adapter_options = {k: v for k, v in adapter_options.items() if v is not None}

        adapter = adapter_class(source, adapter_options)
        service = IngestionService(source, adapter)
        summary = service.run(mark_expired=bool(options.get("mark_expired")))

        self._update_source_health(source, summary)

        self.stdout.write(self.style.SUCCESS(
            f"Ingestion complete: total={summary.total_records}, "
            f"created={summary.created}, updated={summary.updated}, "
            f"skipped={summary.skipped}, failed={summary.failed}, "
            f"fetch_complete={summary.fetch_complete}"
        ))
        if summary.errors:
            for error in summary.errors:
                self.stdout.write(self.style.ERROR(f"  {error}"))

        if summary.failed > 0:
            raise CommandError("Ingestion completed with record failures")

    def _resolve_source(self, options) -> JobSource:
        source_id = options.get("source_id")
        source_name = options.get("source_name")
        create = options.get("create_source")

        if source_id:
            try:
                return JobSource.objects.get(pk=source_id)
            except JobSource.DoesNotExist:
                raise CommandError(f"JobSource with id={source_id} does not exist")

        if source_name:
            try:
                return JobSource.objects.get(name=source_name)
            except JobSource.DoesNotExist:
                if create:
                    return JobSource.objects.create(name=source_name)
                raise CommandError(f"JobSource '{source_name}' does not exist")

        raise CommandError("Either --source-id or --source-name is required")

    def _update_source_health(self, source: JobSource, summary):
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
        source.save(update_fields=[
            "last_ingested_at",
            "last_ingestion_status",
            "last_ingestion_error",
        ])
