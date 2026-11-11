"""Tests for employer seed loading, activation, and scan commands."""
import io
import json
import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Job, JobSource


class MockResponse:
    def __init__(self, data, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


@contextmanager
def temp_seed_file(seeds):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(seeds, f)
        f.flush()
        yield f.name


class LoadEmployerSeedsTests(TestCase):
    @patch("core.discovery.fetch_url")
    @patch("core.discovery.can_fetch", return_value=True)
    def test_creates_inactive_source_after_successful_probe(self, mock_can, mock_fetch):
        mock_fetch.return_value = MockResponse({"jobs": [{"id": 1, "title": "Engineer"}]})
        seeds = [{"name": "ExampleCo", "greenhouse": "exampleco"}]
        with temp_seed_file(seeds) as path:
            out = io.StringIO()
            call_command("load_employer_seeds", file=path, stdout=out)

        source = JobSource.objects.get(name="ExampleCo")
        self.assertFalse(source.is_active)
        self.assertEqual(source.source_type, JobSource.SourceType.ATS)
        self.assertEqual(source.config["adapter_key"], "greenhouse")
        self.assertEqual(source.config["board_token"], "exampleco")
        self.assertIn("Created inactive source", out.getvalue())

    @patch("core.discovery.fetch_url")
    @patch("core.discovery.can_fetch", return_value=True)
    def test_skips_seed_when_probe_fails(self, mock_can, mock_fetch):
        mock_fetch.return_value = MockResponse({"error": "not found"})
        seeds = [{"name": "BadCo", "greenhouse": "badco"}]
        with temp_seed_file(seeds) as path:
            out = io.StringIO()
            call_command("load_employer_seeds", file=path, stdout=out)

        self.assertFalse(JobSource.objects.filter(name="BadCo").exists())
        self.assertIn("No verified boards", out.getvalue())


class ActivateSourcesTests(TestCase):
    def test_activates_sources_by_id(self):
        source = JobSource.objects.create(name="Inactive", is_active=False)
        out = io.StringIO()
        call_command("activate_sources", str(source.pk), stdout=out)
        source.refresh_from_db()
        self.assertTrue(source.is_active)
        self.assertIn("Activated 1 source(s)", out.getvalue())

    def test_fails_for_missing_id(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command("activate_sources", "99999")


class ScanJobsTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(
            name="Greenhouse Example",
            source_type=JobSource.SourceType.ATS,
            access_method=JobSource.AccessMethod.API,
            is_active=True,
            config={"adapter_key": "greenhouse", "board_token": "example"},
        )

    @patch("core.adapters.impl.greenhouse.fetch_url")
    def test_scan_creates_jobs_and_updates_source_health(self, mock_fetch):
        mock_fetch.return_value = MockResponse(
            {
                "jobs": [
                    {
                        "id": 101,
                        "title": "Data Analyst",
                        "location": {"name": "Nairobi, Kenya"},
                        "absolute_url": "https://example.com/jobs/101",
                        "updated_at": "2026-11-01T00:00:00Z",
                    }
                ]
            }
        )
        out = io.StringIO()
        call_command("scan_jobs", stdout=out)

        self.assertTrue(
            Job.objects.filter(source=self.source, external_id="101").exists()
        )
        job = Job.objects.get(source=self.source, external_id="101")
        self.assertEqual(job.title, "Data Analyst")
        self.assertEqual(job.source_listing_status, Job.SourceListingStatus.ACTIVE)

        self.source.refresh_from_db()
        self.assertEqual(self.source.last_ingestion_status, JobSource.IngestionStatus.SUCCESS)
        self.assertIn("Scan complete", out.getvalue())
        self.assertIn("Kenya", out.getvalue())

    @patch("core.adapters.impl.greenhouse.fetch_url")
    def test_scan_respects_max_sources(self, mock_fetch):
        JobSource.objects.create(
            name="Second Source",
            source_type=JobSource.SourceType.ATS,
            access_method=JobSource.AccessMethod.API,
            is_active=True,
            config={"adapter_key": "greenhouse", "board_token": "second"},
        )
        mock_fetch.return_value = MockResponse({"jobs": []})
        out = io.StringIO()
        call_command("scan_jobs", "--max-sources=1", stdout=out)

        # Only one source should be scanned.
        scanned = out.getvalue().count("fetch_complete=")
        self.assertEqual(scanned, 1)

    @patch("core.adapters.impl.greenhouse.fetch_url")
    def test_scan_marks_unseen_jobs_expired(self, mock_fetch):
        existing = Job.objects.create(
            source=self.source,
            title="Old Job",
            company="Co",
            external_id="old",
            source_listing_status=Job.SourceListingStatus.ACTIVE,
        )
        mock_fetch.return_value = MockResponse(
            {
                "jobs": [
                    {
                        "id": 202,
                        "title": "New Job",
                        "location": {"name": "Remote"},
                        "absolute_url": "https://example.com/jobs/202",
                        "updated_at": "2026-11-01T00:00:00Z",
                    }
                ]
            }
        )
        call_command("scan_jobs")

        existing.refresh_from_db()
        self.assertEqual(existing.source_listing_status, Job.SourceListingStatus.EXPIRED)

    @patch("core.adapters.impl.greenhouse.fetch_url")
    def test_scan_counts_multi_location_jobs_for_each_country(self, mock_fetch):
        mock_fetch.return_value = MockResponse(
            {
                "jobs": [
                    {
                        "id": 301,
                        "title": "Global Role",
                        "location": {"name": "New York, NY, London, Toronto"},
                        "absolute_url": "https://example.com/jobs/301",
                        "updated_at": "2026-11-01T00:00:00Z",
                    }
                ]
            }
        )
        out = io.StringIO()
        call_command("scan_jobs", stdout=out)
        self.assertIn("United States", out.getvalue())
        self.assertIn("United Kingdom", out.getvalue())
        self.assertIn("Canada", out.getvalue())

    @patch("core.adapters.impl.greenhouse.fetch_url")
    def test_scan_prints_unmapped_locations(self, mock_fetch):
        mock_fetch.return_value = MockResponse(
            {
                "jobs": [
                    {
                        "id": 302,
                        "title": "Mystery Role",
                        "location": {"name": "Orbital Station Alpha"},
                        "absolute_url": "https://example.com/jobs/302",
                        "updated_at": "2026-11-01T00:00:00Z",
                    }
                ]
            }
        )
        out = io.StringIO()
        call_command("scan_jobs", "--show-unmapped-locations", stdout=out)
        self.assertIn("Top unmapped locations", out.getvalue())
        self.assertIn("Orbital Station Alpha", out.getvalue())
