"""Probe public ATS boards for a company name and print seed entries."""
import json
from urllib.parse import urlparse

from django.core.management.base import BaseCommand

from core.discovery import probe_employer_name, seed_entry_for_result


class Command(BaseCommand):
    help = "Probe Greenhouse, Lever and Ashby for a company name and print seed entries."

    STATUS_LABELS = {
        "found": "found",
        "not_found": "not found",
        "forbidden": "access denied / disallowed",
        "error": "error",
    }

    def add_arguments(self, parser):
        parser.add_argument("company_name", help="Company name to probe")

    def handle(self, *args, **options):
        name = options["company_name"]
        verbose = options.get("verbosity", 1)
        self.stdout.write(f"Probing ATS boards for: {name}")
        outcomes = probe_employer_name(name)

        found_any = False
        for outcome in outcomes:
            label = self.STATUS_LABELS.get(outcome.status, outcome.status)
            self.stdout.write(f"  {outcome.adapter_key}: {label}")
            if verbose >= 2:
                self._print_verbose(outcome)
            if outcome.status == "found" and outcome.result:
                found_any = True
                entry = seed_entry_for_result(name, outcome.result)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    {outcome.result.job_count} job(s) — paste this into employer_seeds.json:"
                    )
                )
                self.stdout.write(f"    {json.dumps(entry, indent=2)}")

        if not found_any:
            self.stdout.write(
                self.style.WARNING("No verified boards found; nothing saved.")
            )
        else:
            self.stdout.write(
                self.style.NOTICE(
                    "Probe complete. This command does not save sources; run load_employer_seeds to import verified entries."
                )
            )

    def _print_verbose(self, outcome):
        """Print host/path, HTTP status and reason for a probe outcome."""
        detail = outcome.detail or "no additional detail"
        status = f"HTTP {outcome.http_status}" if outcome.http_status else "N/A"
        self.stdout.write(f"    host/path: {self._host_path(outcome.adapter_key, outcome.board_value)}")
        self.stdout.write(f"    status: {status}")
        self.stdout.write(f"    outcome: {outcome.status}")
        self.stdout.write(f"    reason: {detail}")

    def _host_path(self, adapter_key: str, board_value: str) -> str:
        if adapter_key == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{board_value}/jobs"
        elif adapter_key == "lever":
            url = f"https://api.lever.co/v0/postings/{board_value}?mode=json"
        elif adapter_key == "ashby":
            url = f"https://api.ashbyhq.com/posting-api/job-board/{board_value}"
        else:
            return "unknown"
        parsed = urlparse(url)
        return f"{parsed.netloc}{parsed.path}"
