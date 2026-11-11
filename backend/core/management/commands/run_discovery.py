"""Batch discovery for a single user.

Generates employer suggestions from the user's profile, verifies suggested
candidates against public ATS boards, and records outcomes. It does not
auto-approve candidates; approved sources are created through the API or
management commands only.
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.discovery.verify import VerificationError, verify_candidate
from core.discovery.verify import PROBES as VERIFY_PROBES
from core.discovery.verify import _build_variants
from core.discovery.suggest import SuggestionError, suggest_employers
from core.models import EmployerCandidate, UserProfile


DEFAULT_MAX_PROBES = 100


class Command(BaseCommand):
    help = "Suggest employers for a user and verify them against public ATS boards."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            required=True,
            help="Django user id to run discovery for",
        )
        parser.add_argument(
            "--max-probes",
            type=int,
            default=DEFAULT_MAX_PROBES,
            help=f"Maximum probe calls per run (default {DEFAULT_MAX_PROBES})",
        )
        parser.add_argument(
            "--no-suggest",
            action="store_true",
            help="Skip LLM suggestion and only verify existing suggested candidates",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without modifying candidates",
        )

    def handle(self, *args, **options):
        user_id = options["user_id"]
        max_probes = options["max_probes"]
        no_suggest = options["no_suggest"]
        dry_run = options["dry_run"]

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist as exc:
            raise CommandError(f"User {user_id} not found") from exc

        profile, _ = UserProfile.objects.get_or_create(user=user)
        if not profile.cv_parsing_consent:
            self.stdout.write(
                self.style.WARNING("User has not given CV parsing consent; skipping.")
            )
            return

        if not no_suggest:
            try:
                suggested = suggest_employers(user, profile)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Suggested {len(suggested)} employer(s) for user {user_id}"
                    )
                )
            except SuggestionError as exc:
                self.stdout.write(self.style.WARNING(f"Suggestion failed: {exc}"))
                suggested = []

        candidates = EmployerCandidate.objects.filter(
            user=user,
            status=EmployerCandidate.Status.SUGGESTED,
        ).order_by("created_at")

        total_candidates = candidates.count()
        if total_candidates == 0:
            self.stdout.write("No suggested candidates to verify.")
            return

        self.stdout.write(f"Verifying up to {total_candidates} candidate(s)")

        probes_used = 0
        outcomes = {
            "found": 0,
            "no_supported_board": 0,
            "blocked_or_error": 0,
            "skipped": 0,
        }

        for candidate in candidates:
            variants = _build_variants(candidate.display_name, candidate.normalized_name)
            probes_needed = len(variants) * len(VERIFY_PROBES)
            if probes_used + probes_needed > max_probes:
                outcomes["skipped"] += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipping {candidate.display_name}: would exceed probe budget"
                    )
                )
                continue

            if dry_run:
                self.stdout.write(
                    f"Would verify {candidate.display_name} ({probes_needed} probes)"
                )
                probes_used += probes_needed
                continue

            try:
                with transaction.atomic():
                    verify_candidate(candidate)
            except VerificationError as exc:
                self.stdout.write(
                    self.style.WARNING(
                        f"Could not verify {candidate.display_name}: {exc}"
                    )
                )
                outcomes["blocked_or_error"] += 1
                continue

            candidate.refresh_from_db()
            probes_used += probes_needed
            if candidate.status == EmployerCandidate.Status.FOUND:
                outcomes["found"] += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Found {candidate.adapter_key} board for {candidate.display_name}"
                    )
                )
            elif candidate.status == EmployerCandidate.Status.NO_SUPPORTED_BOARD:
                outcomes["no_supported_board"] += 1
                self.stdout.write(
                    f"No supported board for {candidate.display_name}"
                )
            else:
                outcomes["blocked_or_error"] += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Verification for {candidate.display_name} blocked or errored"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Discovery complete for user {user_id}: "
                f"found={outcomes['found']}, "
                f"no_supported_board={outcomes['no_supported_board']}, "
                f"blocked_or_error={outcomes['blocked_or_error']}, "
                f"skipped={outcomes['skipped']}, "
                f"probes_used={probes_used}"
            )
        )
