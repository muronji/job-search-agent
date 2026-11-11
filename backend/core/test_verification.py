"""Tests for employer candidate verification."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from core.discovery import ProbeOutcome, ProbeResult
from core.discovery.verify import (
    VerificationError,
    sanitize_board_token,
    verify_candidate,
)
from core.models import EmployerCandidate


class SanitizeTokenTests(TestCase):
    def test_allows_alphanumeric_and_hyphens(self):
        self.assertEqual(sanitize_board_token("Cloud Flare"), "cloud-flare")
        self.assertEqual(sanitize_board_token("Example_Co"), "example-co")
        self.assertEqual(sanitize_board_token("cloudflare"), "cloudflare")

    def test_rejects_dangerous_characters(self):
        self.assertEqual(
            sanitize_board_token("evil/../path"), "evil-path"
        )
        self.assertEqual(
            sanitize_board_token("name%2Fencoded"), "name-encoded"
        )

    def test_none_for_empty(self):
        self.assertIsNone(sanitize_board_token(""))
        self.assertIsNone(sanitize_board_token("!!!"))

    def test_caps_length(self):
        long_name = "a" * 100
        self.assertEqual(len(sanitize_board_token(long_name)), 50)


class VerifyCandidateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='verifyuser', password='pass')
        self.candidate = EmployerCandidate.objects.create(
            user=self.user,
            display_name='Example Co',
            normalized_name='exampleco',
        )

    def _found_outcome(self, adapter_key, variant, count=3):
        result = ProbeResult(
            adapter_key=adapter_key,
            board_value=variant,
            display_name=variant,
            job_count=count,
            raw_data={
                "jobs": [
                    {"title": f"Engineer {i}", "location": "Remote"}
                    for i in range(count)
                ]
            },
        )
        return ProbeOutcome(
            adapter_key=adapter_key,
            board_value=variant,
            status="found",
            result=result,
        )

    def _not_found_outcome(self, adapter_key, variant):
        return ProbeOutcome(
            adapter_key=adapter_key,
            board_value=variant,
            status="not_found",
            http_status=404,
            detail="404",
        )

    def test_found_updates_status_and_sample_jobs(self):
        with patch(
            'core.discovery.probe_greenhouse',
            return_value=self._found_outcome('greenhouse', 'exampleco'),
        ):
            with patch(
                'core.discovery.probe_lever',
                return_value=self._not_found_outcome('lever', 'exampleco'),
            ):
                with patch(
                    'core.discovery.probe_ashby',
                    return_value=self._not_found_outcome('ashby', 'exampleco'),
                ):
                    verify_candidate(self.candidate)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, EmployerCandidate.Status.FOUND)
        self.assertEqual(self.candidate.adapter_key, 'greenhouse')
        self.assertEqual(self.candidate.board_value, 'exampleco')
        self.assertEqual(len(self.candidate.sample_jobs), 3)

    def test_no_supported_board_after_all_404(self):
        for probe in ['probe_greenhouse', 'probe_lever', 'probe_ashby']:
            patcher = patch(
                f'core.discovery.{probe}',
                return_value=self._not_found_outcome(
                    probe.replace('probe_', ''), 'exampleco'
                ),
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        verify_candidate(self.candidate)
        self.candidate.refresh_from_db()
        self.assertEqual(
            self.candidate.status,
            EmployerCandidate.Status.NO_SUPPORTED_BOARD,
        )

    def test_blocked_keeps_suggested(self):
        forbidden = ProbeOutcome(
            adapter_key='greenhouse',
            board_value='exampleco',
            status='forbidden',
            detail='robots.txt disallows',
        )
        with patch(
            'core.discovery.probe_greenhouse', return_value=forbidden
        ):
            with patch(
                'core.discovery.probe_lever',
                return_value=self._not_found_outcome('lever', 'exampleco'),
            ):
                with patch(
                    'core.discovery.probe_ashby',
                    return_value=self._not_found_outcome('ashby', 'exampleco'),
                ):
                    verify_candidate(self.candidate)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, EmployerCandidate.Status.SUGGESTED)
        self.assertIn('forbidden', self.candidate.reason)

    def test_no_reprobe_within_30_days(self):
        self.candidate.status = EmployerCandidate.Status.NO_SUPPORTED_BOARD
        self.candidate.last_probed_at = timezone.now()
        self.candidate.save()
        with self.assertRaises(VerificationError) as ctx:
            verify_candidate(self.candidate)
        self.assertIn("30 days", str(ctx.exception))

    def test_can_reprobe_after_30_days(self):
        self.candidate.status = EmployerCandidate.Status.NO_SUPPORTED_BOARD
        self.candidate.last_probed_at = timezone.now() - timedelta(days=31)
        self.candidate.save()
        for probe in ['probe_greenhouse', 'probe_lever', 'probe_ashby']:
            patcher = patch(
                f'core.discovery.{probe}',
                return_value=self._not_found_outcome(
                    probe.replace('probe_', ''), 'exampleco'
                ),
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        verify_candidate(self.candidate)
        self.candidate.refresh_from_db()
        self.assertEqual(
            self.candidate.status,
            EmployerCandidate.Status.NO_SUPPORTED_BOARD,
        )

    def test_cannot_verify_approved(self):
        self.candidate.status = EmployerCandidate.Status.APPROVED
        self.candidate.save()
        with self.assertRaises(VerificationError):
            verify_candidate(self.candidate)


class VerifyEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='apiuser', password='pass')
        self.client.force_authenticate(user=self.user)
        self.candidate = EmployerCandidate.objects.create(
            user=self.user,
            display_name='Example Co',
            normalized_name='exampleco',
        )

    def test_verify_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            f'/api/employer-candidates/{self.candidate.id}/verify/'
        )
        self.assertEqual(response.status_code, 403)

    def test_verify_returns_candidate(self):
        found = ProbeOutcome(
            adapter_key='greenhouse',
            board_value='exampleco',
            status='found',
            result=ProbeResult(
                'greenhouse', 'exampleco', 'exampleco', 2, raw_data={"jobs": []}
            ),
        )
        with patch('core.discovery.probe_greenhouse', return_value=found):
            with patch(
                'core.discovery.probe_lever',
                return_value=ProbeOutcome(
                    'lever', 'exampleco', 'not_found', http_status=404
                ),
            ):
                with patch(
                    'core.discovery.probe_ashby',
                    return_value=ProbeOutcome(
                        'ashby', 'exampleco', 'not_found', http_status=404
                    ),
                ):
                    response = self.client.post(
                        f'/api/employer-candidates/{self.candidate.id}/verify/'
                    )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'found')
        self.assertEqual(response.data['adapter_key'], 'greenhouse')

    def test_cannot_verify_other_users_candidate(self):
        other = User.objects.create_user(username='other', password='pass')
        other_candidate = EmployerCandidate.objects.create(
            user=other,
            display_name='Other Co',
            normalized_name='otherco',
        )
        response = self.client.post(
            f'/api/employer-candidates/{other_candidate.id}/verify/'
        )
        self.assertEqual(response.status_code, 404)
