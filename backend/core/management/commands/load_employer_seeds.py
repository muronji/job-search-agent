"""Load verified employer ATS seeds into inactive JobSource candidates."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.discovery import probe_employer, seed_entry_for_result
from core.models import JobSource


class Command(BaseCommand):
    help = "Load employer seeds from data/employer_seeds.json after probing each board."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            help="Path to seed JSON file (default: data/employer_seeds.json)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Probe but do not create JobSource rows",
        )

    def handle(self, *args, **options):
        path = options.get("file") or str(settings.BASE_DIR / "data" / "employer_seeds.json")
        file_path = Path(path)
        if not file_path.exists():
            raise CommandError(f"Seed file not found: {path}")

        with file_path.open("r", encoding="utf-8") as f:
            seeds = json.load(f)

        if not isinstance(seeds, list):
            raise CommandError("Seed file must contain a JSON list")

        created = 0
        skipped = 0
        for entry in seeds:
            name = entry.get("name")
            if not name:
                skipped += 1
                self.stdout.write(self.style.WARNING("Skipping seed with no name"))
                continue

            outcomes = probe_employer(entry)
            found_results = [o.result for o in outcomes if o.status == "found"]
            if not found_results:
                skipped += 1
                self.stdout.write(
                    self.style.WARNING(f"No verified boards for {name}; skipped")
                )
                continue

            for result in found_results:
                if options.get("dry_run"):
                    self.stdout.write(
                        f"Would create: {name} ({result.adapter_key}={result.board_value})"
                    )
                    continue

                source, is_new = JobSource.objects.get_or_create(
                    name=name,
                    defaults={
                        "source_type": JobSource.SourceType.ATS,
                        "access_method": JobSource.AccessMethod.API,
                        "is_active": False,
                        "base_url": "",
                        "config": {
                            "adapter_key": result.adapter_key,
                            _config_key(result.adapter_key): result.board_value,
                        },
                    },
                )
                if is_new:
                    created += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Created inactive source: {name} ({result.adapter_key}, {result.job_count} job(s))"
                        )
                    )
                else:
                    self.stdout.write(f"Source already exists: {name} ({result.adapter_key})")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created} source(s), skipped {skipped} seed(s)."
            )
        )


def _config_key(adapter_key: str) -> str:
    if adapter_key == "ashby":
        return "board_name"
    return "board_token"
