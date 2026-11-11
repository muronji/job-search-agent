"""Scan active job sources with per-run caps and expiry."""
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from core.models import JobSource
from core.services.scan import scan_sources


class Command(BaseCommand):
    help = (
        "Scan active job sources, ingest listings, mark unseen listings as expired, "
        "and report per-source and per-country summaries."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-ids",
            help="Comma-separated JobSource ids to scan (default: all active)",
        )
        parser.add_argument(
            "--max-sources",
            type=int,
            default=10,
            help="Maximum number of sources to scan",
        )
        parser.add_argument(
            "--max-pages-per-source",
            type=int,
            default=5,
            help="Maximum pages to fetch per source",
        )
        parser.add_argument(
            "--max-requests",
            type=int,
            default=50,
            help="Maximum total fetch requests across the scan",
        )
        parser.add_argument(
            "--min-interval",
            type=float,
            default=1.0,
            help="Minimum seconds between requests to the same domain",
        )
        parser.add_argument(
            "--show-unmapped-locations",
            action="store_true",
            help="Print the 20 most common location strings with no recognized country",
        )

    def handle(self, *args, **options):
        max_sources = options["max_sources"]
        max_pages = options["max_pages_per_source"]
        max_requests = options["max_requests"]
        min_interval = options["min_interval"]

        sources = self._resolve_sources(options)
        sources = list(sources[:max_sources])
        if not sources:
            self.stdout.write(self.style.WARNING("No sources to scan."))
            return

        results = scan_sources(
            sources,
            max_pages=max_pages,
            max_requests=max_requests,
            min_interval=min_interval,
            mark_expired=True,
        )
        self._print_results(results, options)

    def _resolve_sources(self, options):
        qs = JobSource.objects.filter(is_active=True)
        ids = options.get("source_ids")
        if ids:
            id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
            qs = qs.filter(pk__in=id_list)
        return qs.order_by("name")

    def _print_results(self, results, options):
        total_created = 0
        total_updated = 0
        total_skipped = 0
        total_failed = 0
        total_expired = 0
        total_salary_dropped = 0
        overall_country_counts = defaultdict(int)
        all_unmapped: list[str] = []

        for result in results:
            source = result.source
            summary = result.summary
            total_created += summary.created
            total_updated += summary.updated
            total_skipped += summary.skipped
            total_failed += summary.failed
            total_salary_dropped += summary.salary_dropped

            if summary.fetch_complete and summary.failed == 0 and summary.total_records > 0:
                expired = source.jobs.filter(
                    source_listing_status='expired'
                ).count()
                total_expired += expired

            self.stdout.write(
                f"{source.name}: total={summary.total_records}, "
                f"created={summary.created}, updated={summary.updated}, "
                f"skipped={summary.skipped}, failed={summary.failed}, "
                f"salary_dropped={summary.salary_dropped}, "
                f"fetch_complete={summary.fetch_complete}"
            )
            if summary.errors:
                for error in summary.errors:
                    self.stdout.write(self.style.ERROR(f"  {error}"))
            self.stdout.write(f"  Country counts: {result.country_counts}")
            for country, count in result.country_counts.items():
                overall_country_counts[country] += count
            all_unmapped.extend(result.unmapped)

        self.stdout.write(
            self.style.SUCCESS(
                f"Scan complete: created={total_created}, updated={total_updated}, "
                f"skipped={total_skipped}, failed={total_failed}, "
                f"salary_dropped={total_salary_dropped}, expired={total_expired}"
            )
        )
        self.stdout.write(f"Overall country counts: {dict(overall_country_counts)}")

        if options.get("show_unmapped_locations") or options.get("verbosity", 1) >= 2:
            self._print_unmapped_locations(all_unmapped)

    def _print_unmapped_locations(self, unmapped: list[str]):
        """Print the 20 most common location strings that did not map to a country."""
        if not unmapped:
            return
        counts = defaultdict(int)
        for location in unmapped:
            counts[location] += 1
        top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:20]
        self.stdout.write("Top unmapped locations:")
        for location, count in top:
            self.stdout.write(f"  {count}: {location}")
