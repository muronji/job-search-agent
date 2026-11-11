"""Tests for the Batch-1 scan-agent scorer.

Uses synthetic profiles across different industries, levels and locations.
"""

from django.test import TestCase
from django.contrib.auth.models import User

from core.models import Job, JobSource, UserPreference, UserProfile
from core.scan.scorer import (
    compute_access_score,
    compute_level_score,
    compute_user_years,
    generate_default_scan_profile,
    get_or_build_scan_profile,
    is_confirmed_source,
    map_fit_score,
    score_job,
)
from core.search.ranker import build_intent_from_profile, rank_job
from core.tests import AuthenticatedAPITestCase


class ComputeUserYearsTests(TestCase):
    def test_empty_experience_returns_none(self):
        self.assertIsNone(compute_user_years([]))

    def test_single_role_with_dates(self):
        exp = [{"start_date": "2020-06", "end_date": "2024-06"}]
        self.assertEqual(compute_user_years(exp), 4)

    def test_present_end_date_uses_current_year(self):
        from django.utils import timezone
        current = timezone.now().year
        exp = [{"start_date": "2021", "end_date": "present"}]
        self.assertEqual(compute_user_years(exp), current - 2021)


class GenerateDefaultScanProfileTests(TestCase):
    def test_profile_with_kenya_location(self):
        parsed = {
            "location": "Nairobi, Kenya",
            "experience": [{"title": "Data Analyst", "start_date": "2022", "end_date": "present"}],
        }
        profile = generate_default_scan_profile(parsed)
        self.assertEqual(profile["home_country"], "Kenya")
        self.assertIsNotNone(profile["years_of_experience"])
        countries = [r["country"] for r in profile["access_table"]]
        self.assertIn("Kenya", countries)
        self.assertIn("Remote", countries)
        self.assertIn("Other", countries)
        self.assertEqual(profile["thresholds"]["apply"], 75)

    def test_empty_profile_has_no_home_country(self):
        profile = generate_default_scan_profile({})
        self.assertIsNone(profile["home_country"])
        self.assertIsNone(profile["years_of_experience"])


class AccessScoreTests(TestCase):
    def setUp(self):
        self.scan_profile = {
            "home_country": "Kenya",
            "relocation_countries": ["United Kingdom"],
            "remote_enabled": True,
            "access_table": [
                {"country": "Kenya", "score": 5, "reason": "Home country"},
                {"country": "Remote", "score": 5, "reason": "Remote role"},
                {"country": "United Kingdom", "score": 2, "reason": "Relocation"},
                {"country": "Other", "score": 1, "reason": "Other"},
            ],
        }

    def test_home_country_scores_five(self):
        job = Job(title="Analyst", company="Co", location="Nairobi, Kenya")
        score, reason = compute_access_score(job, self.scan_profile)
        self.assertEqual(score, 5)
        self.assertIn("Home", reason)

    def test_remote_open_scores_five(self):
        job = Job(
            title="Remote Analyst",
            company="Co",
            location="Remote",
            work_arrangement=Job.WorkArrangement.REMOTE,
        )
        score, reason = compute_access_score(job, self.scan_profile)
        self.assertEqual(score, 5)

    def test_relocation_country_default_is_two(self):
        job = Job(title="Analyst", company="Co", location="London, UK")
        score, reason = compute_access_score(job, self.scan_profile)
        self.assertEqual(score, 2)

    def test_relocation_with_sponsorship_bumps_to_three(self):
        job = Job(
            title="Analyst",
            company="Co",
            location="London, UK",
            description="We offer visa sponsorship.",
        )
        score, reason = compute_access_score(job, self.scan_profile)
        self.assertEqual(score, 3)

    def test_blocked_phrase_scores_one(self):
        job = Job(
            title="Analyst",
            company="Co",
            location="Remote",
            description="US citizenship required.",
        )
        score, reason = compute_access_score(job, self.scan_profile)
        self.assertEqual(score, 1)
        self.assertIn("blocked", reason)

    def test_unknown_location_is_neutral(self):
        job = Job(title="Analyst", company="Co", location="")
        score, reason = compute_access_score(job, self.scan_profile)
        self.assertIsNone(score)
        self.assertIn("not mapped", reason)


class LevelScoreTests(TestCase):
    def setUp(self):
        self.scan_profile = {"years_of_experience": 3}

    def test_entry_role_scores_five(self):
        job = Job(title="Graduate Data Analyst", company="Co")
        score, reason = compute_level_score(job, self.scan_profile)
        self.assertEqual(score, 5)

    def test_required_years_below_user_scores_high(self):
        job = Job(title="Data Analyst", company="Co", description="1 year of experience.")
        score, _ = compute_level_score(job, self.scan_profile)
        self.assertGreaterEqual(score, 4)

    def test_required_years_above_user_scores_low(self):
        job = Job(title="Data Analyst", company="Co", description="8 years of experience.")
        score, _ = compute_level_score(job, self.scan_profile)
        self.assertLessEqual(score, 2)

    def test_senior_title_lowers_level(self):
        job = Job(title="Senior Data Analyst", company="Co", description="5 years of experience.")
        score, reason = compute_level_score(job, self.scan_profile)
        self.assertLessEqual(score, 2)
        self.assertIn("senior", reason)

    def test_unknown_years_returns_none(self):
        job = Job(title="Data Analyst", company="Co")
        score, reason = compute_level_score(job, self.scan_profile)
        self.assertIsNone(score)
        self.assertIn("not stated", reason)


class GateTests(TestCase):
    def setUp(self):
        self.scan_profile = {
            "years_of_experience": 3,
            "gates": {
                "exclude_countries": ["Uganda"],
                "exclude_role_types": ["volunteer"],
                "exclude_unpaid": True,
                "max_years_required": 3,
                "clearance_phrases": ["security clearance"],
                "missing_certifications": ["PMP"],
            },
        }

    def test_excluded_country_ignores(self):
        job = Job(title="Analyst", company="Co", location="Kampala, Uganda")
        result = apply_gates_from_profile(job, self.scan_profile)
        self.assertEqual(result["verdict_cap"], "ignore")
        self.assertTrue(any("excluded country" in r for r in result["ignore_reasons"]))

    def test_unpaid_role_ignores(self):
        job = Job(title="Helper", company="Org", employment_type=Job.EmploymentType.VOLUNTEER)
        result = apply_gates_from_profile(job, self.scan_profile)
        self.assertEqual(result["verdict_cap"], "ignore")

    def test_years_required_caps_track(self):
        job = Job(title="Senior Analyst", company="Co", description="5 years of experience.")
        result = apply_gates_from_profile(job, self.scan_profile)
        self.assertEqual(result["verdict_cap"], "track")

    def test_clearance_caps_track(self):
        job = Job(title="Analyst", company="Co", description="Security clearance required.")
        result = apply_gates_from_profile(job, self.scan_profile)
        self.assertEqual(result["verdict_cap"], "track")


class ConfirmedSourceTests(TestCase):
    def test_ats_api_is_confirmed(self):
        source = JobSource(
            name="greenhouse:test",
            source_type=JobSource.SourceType.ATS,
            access_method=JobSource.AccessMethod.API,
        )
        source.save()
        job = Job(title="Dev", company="Co", source=source)
        self.assertTrue(is_confirmed_source(job))

    def test_feed_is_not_confirmed(self):
        source = JobSource(
            name="remoteok",
            source_type=JobSource.SourceType.FEED,
            access_method=JobSource.AccessMethod.RSS,
        )
        source.save()
        job = Job(title="Dev", company="Co", source=source)
        self.assertFalse(is_confirmed_source(job))


class ScoreJobTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="scorer", password="p")
        UserProfile.objects.create(
            user=self.user,
            parsed_profile={
                "location": "Nairobi, Kenya",
                "skills": ["Python", "SQL"],
                "experience": [{"title": "Data Analyst", "start_date": "2022", "end_date": "present"}],
                "preferences": {
                    "job_titles": ["Data Analyst"],
                    "locations": ["Kenya"],
                    "work_arrangements": ["remote"],
                },
            },
        )
        self.scan_profile = get_or_build_scan_profile(self.user)

    def test_confirmed_job_can_apply(self):
        source = JobSource.objects.create(
            name="greenhouse:test",
            source_type=JobSource.SourceType.ATS,
            access_method=JobSource.AccessMethod.API,
        )
        job = Job(
            title="Remote Data Analyst",
            company="Co",
            source=source,
            location="Kenya",
            work_arrangement=Job.WorkArrangement.REMOTE,
            description="Python and SQL.",
        )
        intent = build_intent_from_profile(self.user)
        fit = rank_job(job, intent)
        result = score_job(job, fit, self.scan_profile)
        self.assertGreaterEqual(result["total_score"], 75)
        self.assertEqual(result["verdict"], "apply")
        self.assertTrue(result["is_confirmed"])
        self.assertFalse(result["is_lead"])

    def test_unconfirmed_source_is_lead(self):
        source = JobSource.objects.create(
            name="remoteok",
            source_type=JobSource.SourceType.FEED,
            access_method=JobSource.AccessMethod.RSS,
        )
        job = Job(
            title="Remote Data Analyst",
            company="Co",
            source=source,
            location="Kenya",
            work_arrangement=Job.WorkArrangement.REMOTE,
            description="Python and SQL.",
        )
        intent = build_intent_from_profile(self.user)
        fit = rank_job(job, intent)
        result = score_job(job, fit, self.scan_profile)
        self.assertTrue(result["is_lead"])
        self.assertNotEqual(result["verdict"], "apply")


class ScanProfileAPITests(AuthenticatedAPITestCase):
    def test_patch_scan_profile(self):
        self.client.get("/api/preferences/")
        payload = {
            "scan_profile": {
                "home_country": "Kenya",
                "relocation_countries": ["United Kingdom"],
                "remote_enabled": True,
                "years_of_experience": 3,
                "access_table": [
                    {"country": "Kenya", "score": 5, "reason": "Home"},
                    {"country": "Remote", "score": 5, "reason": "Remote"},
                ],
                "gates": {
                    "exclude_countries": [],
                    "exclude_role_types": [],
                    "exclude_unpaid": True,
                    "max_years_required": 5,
                    "clearance_phrases": ["security clearance"],
                    "missing_certifications": [],
                },
                "thresholds": {"apply": 75, "track": 50, "ignore": 30},
                "role_families": [],
            }
        }
        response = self.client.patch("/api/preferences/", payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scan_profile"]["home_country"], "Kenya")
        self.assertEqual(response.data["scan_profile"]["thresholds"]["apply"], 75)

    def test_invalid_scan_profile_rejected(self):
        self.client.get("/api/preferences/")
        response = self.client.patch(
            "/api/preferences/",
            {"scan_profile": {"thresholds": {"apply": 999}}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class SearchIntegrationTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = JobSource.objects.create(name="Test Source")
        UserProfile.objects.create(
            user=self.user,
            parsed_profile={
                "location": "Nairobi, Kenya",
                "skills": ["Python"],
                "experience": [{"title": "Data Analyst", "start_date": "2022", "end_date": "present"}],
                "preferences": {
                    "job_titles": ["Data Analyst"],
                    "locations": ["Kenya"],
                    "work_arrangements": ["remote"],
                },
            },
        )
        Job.objects.create(
            title="Remote Data Analyst",
            company="Co",
            source=self.source,
            location="Nairobi, Kenya",
            work_arrangement=Job.WorkArrangement.REMOTE,
            description="Python and SQL.",
        )
        Job.objects.create(
            title="Chef",
            company="Kitchen",
            source=self.source,
            location="Paris, France",
            description="Cooking.",
        )

    def test_profile_does_not_hide_jobs(self):
        response = self.client.get("/api/search/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_matching_job_scores_higher(self):
        response = self.client.get("/api/search/")
        results = response.data["results"]
        match = next(r for r in results if r["title"] == "Remote Data Analyst")
        miss = next(r for r in results if r["title"] == "Chef")
        self.assertGreater(match["total_score"], miss["total_score"])

    def test_scan_profile_generated_on_search(self):
        self.assertEqual(UserPreference.objects.count(), 0)
        response = self.client.get("/api/search/")
        self.assertEqual(response.status_code, 200)
        pref = UserPreference.objects.get(user=self.user)
        self.assertIn("access_table", pref.scan_profile)
        self.assertEqual(pref.scan_profile["home_country"], "Kenya")


# Helper that exposes the internal gate logic used by the unit tests above.
def apply_gates_from_profile(job, scan_profile):
    from core.scan.scorer import apply_gates
    return apply_gates(job, scan_profile)
