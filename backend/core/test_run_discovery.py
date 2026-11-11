"""Tests for the run_discovery management command."""
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from core.discovery import ProbeOutcome, ProbeResult
from core.models import EmployerCandidate, UserProfile


class RunDiscoveryCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='discoveruser', password='pass')
        self.profile, _ = UserProfile.objects.get_or_create(
            user=self.user,
            defaults={
                'cv_parsing_consent': True,
                'parsed_profile': {
                    'skills': ['Python'],
                    'preferences': {
                        'job_titles': ['Engineer'],
                        'locations': ['Kenya'],
                    },
                },
            },
        )

    def _call(self, *args):
        out = StringIO()
        err = StringIO()
        call_command('run_discovery', *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def _not_found(self, adapter, variant):
        return ProbeOutcome(adapter, variant, 'not_found', http_status=404)

    def test_requires_user_id(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._call()

    def test_skips_without_consent(self):
        self.profile.cv_parsing_consent = False
        self.profile.save()
        out, _ = self._call('--user-id', str(self.user.id))
        self.assertIn('consent', out.lower())

    def test_verifies_existing_suggested_candidates(self):
        EmployerCandidate.objects.create(
            user=self.user,
            display_name='Example Co',
            normalized_name='exampleco',
            status=EmployerCandidate.Status.SUGGESTED,
        )
        with patch(
            'core.discovery.probe_greenhouse',
            return_value=ProbeOutcome(
                'greenhouse',
                'exampleco',
                'found',
                result=ProbeResult(
                    'greenhouse', 'exampleco', 'exampleco', 2, raw_data={"jobs": []}
                ),
            ),
        ):
            with patch(
                'core.discovery.probe_lever',
                return_value=self._not_found('lever', 'exampleco'),
            ):
                with patch(
                    'core.discovery.probe_ashby',
                    return_value=self._not_found('ashby', 'exampleco'),
                ):
                    out, _ = self._call('--user-id', str(self.user.id), '--no-suggest')
        self.assertIn('found=1', out)
        self.assertIn('greenhouse board for Example Co', out)

    def test_respects_max_probes(self):
        for i in range(5):
            EmployerCandidate.objects.create(
                user=self.user,
                display_name=f'Co {i}',
                normalized_name=f'co{i}',
                status=EmployerCandidate.Status.SUGGESTED,
            )
        out, _ = self._call('--user-id', str(self.user.id), '--no-suggest', '--max-probes', '6')
        self.assertIn('would exceed probe budget', out)

    def test_no_supported_board_reported(self):
        EmployerCandidate.objects.create(
            user=self.user,
            display_name='No Board Co',
            normalized_name='noboardco',
            status=EmployerCandidate.Status.SUGGESTED,
        )
        for probe in ['probe_greenhouse', 'probe_lever', 'probe_ashby']:
            patcher = patch(
                f'core.discovery.{probe}',
                return_value=self._not_found(probe.replace('probe_', ''), 'noboardco'),
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        out, _ = self._call('--user-id', str(self.user.id), '--no-suggest')
        self.assertIn('no_supported_board=1', out)

    def test_generates_suggestions(self):
        with patch(
            'core.management.commands.run_discovery.suggest_employers',
            return_value=[
                EmployerCandidate(
                    user=self.user,
                    display_name='Suggested Co',
                    normalized_name='suggestedco',
                )
            ],
        ):
            out, _ = self._call('--user-id', str(self.user.id))
        self.assertIn('Suggested 1 employer', out)
