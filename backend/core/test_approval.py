"""Tests for candidate approval, rejection, and scan-now."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APITestCase

from core.adapters.base import BaseAdapter
from core.discovery.approval import ApprovalError, approve_candidate, reject_candidate
from core.models import EmployerCandidate, Job, JobSource


class ApprovalServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='approveuser', password='pass')
        self.candidate = EmployerCandidate.objects.create(
            user=self.user,
            display_name='Example Co',
            normalized_name='exampleco',
            status=EmployerCandidate.Status.FOUND,
            adapter_key='greenhouse',
            board_value='exampleco',
            sample_jobs=[{"title": "Engineer", "location": "Remote"}],
        )

    def test_approve_creates_inactive_source(self):
        source = approve_candidate(self.candidate)
        self.assertEqual(source.source_type, JobSource.SourceType.ATS)
        self.assertEqual(source.access_method, JobSource.AccessMethod.API)
        self.assertFalse(source.is_active)
        self.assertEqual(source.config['adapter_key'], 'greenhouse')
        self.assertEqual(source.config['board_token'], 'exampleco')
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, EmployerCandidate.Status.APPROVED)

    def test_approve_deduplicates_by_adapter_and_token(self):
        source1 = approve_candidate(self.candidate)
        other_candidate = EmployerCandidate.objects.create(
            user=self.user,
            display_name='Example Company',
            normalized_name='examplecompany',
            status=EmployerCandidate.Status.FOUND,
            adapter_key='greenhouse',
            board_value='exampleco',
        )
        source2 = approve_candidate(other_candidate)
        self.assertEqual(source1.id, source2.id)

    def test_approve_requires_found_status(self):
        self.candidate.status = EmployerCandidate.Status.SUGGESTED
        self.candidate.save()
        with self.assertRaises(ApprovalError):
            approve_candidate(self.candidate)

    def test_reject_candidate(self):
        reject_candidate(self.candidate)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.status, EmployerCandidate.Status.REJECTED)


class FakeAdapter(BaseAdapter):
    def __init__(self, source, options):
        super().__init__(source, options)
        self.fetch_complete = True

    def fetch(self):
        return [
            {
                'title': 'Test Job',
                'company': self.source.name,
                'location': 'Remote',
                '_source_name': self.source.name,
                'external_id': '1',
            }
        ]

    def normalize(self, raw):
        return {
            'title': raw['title'],
            'company': raw['company'],
            'location': raw['location'],
            'description': '',
            'industry': '',
            'source_url': '',
            'employment_type': '',
            'work_arrangement': '',
            'seniority': '',
            'salary_currency': '',
            'salary_period': '',
            '_source_name': raw['_source_name'],
            'external_id': raw['external_id'],
        }


class ApprovalEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='apiuser', password='pass')
        self.client.force_authenticate(user=self.user)
        self.candidate = EmployerCandidate.objects.create(
            user=self.user,
            display_name='Example Co',
            normalized_name='exampleco',
            status=EmployerCandidate.Status.FOUND,
            adapter_key='greenhouse',
            board_value='exampleco',
        )

    def test_approve_endpoint(self):
        response = self.client.post(
            f'/api/employer-candidates/{self.candidate.id}/approve/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['candidate']['status'], 'approved')
        self.assertEqual(response.data['source']['name'], 'greenhouse:exampleco')

    def test_approve_requires_found(self):
        self.candidate.status = EmployerCandidate.Status.SUGGESTED
        self.candidate.save()
        response = self.client.post(
            f'/api/employer-candidates/{self.candidate.id}/approve/'
        )
        self.assertEqual(response.status_code, 400)

    def test_reject_endpoint(self):
        response = self.client.post(
            f'/api/employer-candidates/{self.candidate.id}/reject/'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'rejected')

    def test_cannot_approve_other_users_candidate(self):
        other = User.objects.create_user(username='other', password='pass')
        other_candidate = EmployerCandidate.objects.create(
            user=other,
            display_name='Other Co',
            normalized_name='otherco',
            status=EmployerCandidate.Status.FOUND,
            adapter_key='greenhouse',
            board_value='otherco',
        )
        response = self.client.post(
            f'/api/employer-candidates/{other_candidate.id}/approve/'
        )
        self.assertEqual(response.status_code, 404)

    def test_scan_now_requires_approved_sources(self):
        response = self.client.post('/api/employer-candidates/scan_now/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('No approved sources', response.data['detail'])

    @patch('core.services.scan.get_adapter_class', return_value=FakeAdapter)
    def test_scan_now_returns_summaries(self, mock_get_adapter):
        source = JobSource.objects.create(
            name='greenhouse:exampleco',
            source_type=JobSource.SourceType.ATS,
            access_method=JobSource.AccessMethod.API,
            config={'adapter_key': 'greenhouse', 'board_token': 'exampleco'},
        )
        self.candidate.status = EmployerCandidate.Status.APPROVED
        self.candidate.save()

        response = self.client.post('/api/employer-candidates/scan_now/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        summary = response.data['results'][0]['summary']
        self.assertEqual(summary['created'], 1, f"Summary: {summary}")
        self.assertTrue(
            Job.objects.filter(source=source, title='Test Job').exists()
        )
