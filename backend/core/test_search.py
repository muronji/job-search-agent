"""Tests for read-only search and stats endpoints."""
from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from core.models import Job, JobSource, UserJob, UserProfile
from core.search.ranker import build_intent_from_profile, rank_job
from core.tests import AuthenticatedAPITestCase


class SearchViewTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = JobSource.objects.create(name="Test Source")
        Job.objects.create(
            title="Remote Senior Nurse",
            company="Hospital",
            source=self.source,
            employment_type=Job.EmploymentType.FULL_TIME,
            work_arrangement=Job.WorkArrangement.REMOTE,
            seniority=Job.Seniority.SENIOR,
        )
        Job.objects.create(
            title="Junior Accountant",
            company="Firm",
            source=self.source,
            employment_type=Job.EmploymentType.FULL_TIME,
            work_arrangement=Job.WorkArrangement.ONSITE,
            seniority=Job.Seniority.JUNIOR,
        )

    def test_search_by_keyword(self):
        response = self.client.get("/api/search/?q=Nurse")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Remote Senior Nurse")

    def test_search_by_work_arrangement_token(self):
        response = self.client.get("/api/search/?q=remote")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["work_arrangement"], "remote")

    def test_empty_search_returns_all(self):
        response = self.client.get("/api/search/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)


class StatsViewTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = JobSource.objects.create(name="Test Source")
        self.job_a = Job.objects.create(
            title="Job A",
            company="Co",
            source=self.source,
            application_status=Job.ApplicationStatus.SAVED,
        )
        self.job_b = Job.objects.create(
            title="Job B",
            company="Co",
            source=self.source,
            source_listing_status=Job.SourceListingStatus.EXPIRED,
        )
        UserJob.objects.create(
            user=self.user,
            job=self.job_a,
            application_status=Job.ApplicationStatus.APPLIED,
        )

    def test_summary_counts(self):
        response = self.client.get("/api/stats/summary/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_jobs"], 2)
        self.assertEqual(response.data["expired_jobs"], 1)
        self.assertEqual(response.data["saved_jobs"], 1)
        self.assertEqual(response.data["applied_jobs"], 1)
        self.assertEqual(response.data["sources"], 1)

    def test_filter_options(self):
        response = self.client.get("/api/stats/filters/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("employment_type", response.data)
        self.assertIn("work_arrangement", response.data)
        self.assertIn("application_status", response.data)

    def test_source_status(self):
        response = self.client.get("/api/sources/status/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["job_count"], 2)
        self.assertEqual(response.data[0]["expired_job_count"], 1)


class GoneFilterTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = JobSource.objects.create(name="Test Source")
        Job.objects.create(
            title="Active Job",
            company="Co",
            source=self.source,
            source_listing_status=Job.SourceListingStatus.ACTIVE,
        )
        Job.objects.create(
            title="Gone Job",
            company="Co",
            source=self.source,
            source_listing_status=Job.SourceListingStatus.EXPIRED,
        )

    def test_gone_filter(self):
        response = self.client.get("/api/jobs/?gone=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Gone Job")


class CombinedQueryTests(APITestCase):
    def test_combined_salary_location_seniority(self):
        source = JobSource.objects.create(name="Test Source")
        Job.objects.create(
            title="Senior Nurse",
            company="Hospital",
            source=source,
            location="New York, NY",
            seniority=Job.Seniority.SENIOR,
            salary_min=80000,
            salary_max=120000,
            salary_currency="USD",
        )
        Job.objects.create(
            title="Junior Nurse",
            company="Hospital",
            source=source,
            location="New York, NY",
            seniority=Job.Seniority.JUNIOR,
            salary_min=50000,
            salary_max=70000,
            salary_currency="USD",
        )
        Job.objects.create(
            title="Senior Engineer",
            company="Tech Co",
            source=source,
            location="San Francisco, CA",
            seniority=Job.Seniority.SENIOR,
            salary_min=90000,
            salary_max=130000,
            salary_currency="USD",
        )

        from core.search.intent import SearchIntent
        from core.search.query import intent_to_queryset

        intent = SearchIntent(
            seniority_levels=["senior"],
            locations=["New York"],
            salary_min=75000,
        )
        titles = list(intent_to_queryset(intent).values_list("title", flat=True))
        self.assertEqual(titles, ["Senior Nurse"])


class RankingTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = JobSource.objects.create(name="Test Source")
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={
                "parsed_profile": {
                    "skills": ["Python", "SQL"],
                    "location": "Nairobi",
                    "experience": [{"title": "Data Analyst"}],
                    "preferences": {
                        "job_titles": ["Data Analyst"],
                        "locations": ["Kenya"],
                        "work_arrangements": ["remote"],
                        "min_salary": 50000,
                    },
                }
            },
        )
        self.match_job = Job.objects.create(
            title="Remote Data Analyst",
            company="Co",
            source=self.source,
            location="Kenya",
            work_arrangement=Job.WorkArrangement.REMOTE,
            salary_min=60000,
            salary_max=90000,
            salary_currency="USD",
            description="Need Python and SQL skills.",
        )
        self.miss_job = Job.objects.create(
            title="Chef",
            company="Kitchen",
            source=self.source,
            location="France",
            work_arrangement=Job.WorkArrangement.ONSITE,
            description="Cooking.",
        )

    def test_search_returns_ranked_results(self):
        response = self.client.get("/api/search/")
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        # Profile ranks jobs but must never hide them.
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Remote Data Analyst")
        self.assertIn("total_score", results[0])
        self.assertIn("fit_score", results[0])
        self.assertIn("access_score", results[0])
        self.assertIn("level_score", results[0])
        self.assertIn("verdict", results[0])
        self.assertIn("matched_criteria", results[0])
        self.assertIn("unmatched_criteria", results[0])
        self.assertIn("unknown_criteria", results[0])
        self.assertTrue(results[0]["heuristic"])

    def test_search_returns_all_jobs_with_profile(self):
        """A profile with Kenya and Data Analyst still returns every job."""
        response = self.client.get("/api/search/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        titles = [r["title"] for r in response.data["results"]]
        self.assertIn("Remote Data Analyst", titles)
        self.assertIn("Chef", titles)

    def test_search_foreign_job_has_low_score(self):
        """A job in another country is returned but scores lower."""
        response = self.client.get("/api/search/")
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        match = next(r for r in results if r["title"] == "Remote Data Analyst")
        miss = next(r for r in results if r["title"] == "Chef")
        self.assertGreater(match["total_score"], miss["total_score"])

    def test_rank_job_matches_known_criteria(self):
        intent = build_intent_from_profile(self.user)
        rank = rank_job(self.match_job, intent)
        self.assertGreaterEqual(rank["score"], 0)
        self.assertLessEqual(rank["score"], 100)
        self.assertIn("title", rank["matched_criteria"])
        self.assertTrue(any(c.startswith("skills") for c in rank["matched_criteria"]))
        self.assertIn("location", rank["matched_criteria"])
        self.assertIn("remote", rank["matched_criteria"])
        self.assertIn("salary", rank["matched_criteria"])

    def test_rank_job_reports_unknown_for_missing_data(self):
        intent = build_intent_from_profile(self.user)
        rank = rank_job(self.miss_job, intent)
        self.assertIn("salary", rank["unknown_criteria"])

    def test_skill_aliases_match(self):
        intent = build_intent_from_profile(self.user)
        intent.skills = ["R", "Go"]
        job = Job.objects.create(
            title="Engineer",
            company="Co",
            source=self.source,
            description="We use R and golang daily.",
        )
        rank = rank_job(job, intent)
        matched_skills = " ".join(rank["matched_criteria"])
        self.assertIn("R", matched_skills)
        self.assertIn("Go", matched_skills)


class RankingIsolationTests(APITestCase):
    def test_ranking_uses_own_profile_not_others(self):
        user_a = User.objects.create_user(username="ra", password="p")
        user_b = User.objects.create_user(username="rb", password="p")
        UserProfile.objects.create(
            user=user_a,
            parsed_profile={"skills": ["Python"]},
        )
        UserProfile.objects.create(
            user=user_b,
            parsed_profile={"skills": ["Cooking"]},
        )
        source = JobSource.objects.create(name="Source")
        job = Job.objects.create(
            title="Dev",
            company="Co",
            source=source,
            description="Python.",
        )
        intent_a = build_intent_from_profile(user_a)
        intent_b = build_intent_from_profile(user_b)
        self.assertTrue(any(c.startswith("skills") for c in rank_job(job, intent_a)["matched_criteria"]))
        self.assertFalse(any(c.startswith("skills") for c in rank_job(job, intent_b)["matched_criteria"]))
