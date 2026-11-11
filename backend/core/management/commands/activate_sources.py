"""Activate selected JobSource rows by id."""
from django.core.management.base import BaseCommand, CommandError

from core.models import JobSource


class Command(BaseCommand):
    help = "Activate one or more JobSource rows by id."

    def add_arguments(self, parser):
        parser.add_argument(
            "source_ids",
            nargs="+",
            type=int,
            help="JobSource ids to activate",
        )

    def handle(self, *args, **options):
        ids = options["source_ids"]
        sources = JobSource.objects.filter(pk__in=ids)
        found_ids = set(sources.values_list("pk", flat=True))
        missing = set(ids) - found_ids
        if missing:
            raise CommandError(f"JobSource ids not found: {sorted(missing)}")

        count = sources.update(is_active=True)
        for source in sources:
            self.stdout.write(self.style.SUCCESS(f"Activated {source.name} (id={source.pk})"))
        self.stdout.write(self.style.SUCCESS(f"Activated {count} source(s)."))
