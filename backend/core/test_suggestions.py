"""Tests for employer suggestion service and endpoint."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from core.discovery.suggest import (
    SuggestionError,
    _build_prompt_inputs,
    _normalize_name,
    suggest_employers,
)
from core.llm import FakeProvider, LLMError
from core.models import EmployerCandidate, LLMUsageLog, UserProfile


class SuggestionServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='suggestuser', password='pass')
        self.profile, _ = UserProfile.objects.get_or_create(
            user=self.user,
            defaults={
                'cv_parsing_consent': True,
                'parsed_profile': {
                    'headline': 'Data Analyst',
                    'skills': ['Python', 'SQL', 'Excel'],
                    'preferences': {
                        'job_titles': ['Data Analyst', 'Data Scientist'],
                        'locations': ['Kenya', 'Remote'],
                    },
                },
            },
        )
        cache.clear()

    def _provider(self, names):
        return FakeProvider(
            response_data={"employers": names},
        )

    def test_requires_consent(self):
        self.profile.cv_parsing_consent = False
        self.profile.save()
        with self.assertRaises(SuggestionError):
            suggest_employers(self.user, self.profile)

    def test_creates_candidates(self):
        provider = self._provider(["Example Co", "Another Inc"])
        candidates = suggest_employers(self.user, self.profile, provider=provider)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].display_name, "Example Co")
        self.assertEqual(candidates[0].status, EmployerCandidate.Status.SUGGESTED)
        self.assertEqual(EmployerCandidate.objects.filter(user=self.user).count(), 2)

    def test_deduplicates_by_normalized_name(self):
        provider = self._provider(["Example Co", "example co", "ExampleCo"])
        candidates = suggest_employers(self.user, self.profile, provider=provider)
        # Returned list is deduplicated; DB only has one row.
        self.assertEqual(len(candidates), 1)
        self.assertEqual(EmployerCandidate.objects.filter(user=self.user).count(), 1)

    def test_caches_results(self):
        provider = self._provider(["Cached Co"])
        suggest_employers(self.user, self.profile, provider=provider)
        self.assertEqual(len(provider.calls), 1)
        # Second call with same inputs should not call the provider again.
        provider2 = self._provider(["Different Co"])
        candidates = suggest_employers(self.user, self.profile, provider=provider2)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].display_name, "Cached Co")
        self.assertEqual(len(provider2.calls), 0)

    def test_force_refresh_allows_new_call(self):
        provider = self._provider(["First Co"])
        suggest_employers(self.user, self.profile, provider=provider)
        provider2 = self._provider(["Second Co"])
        candidates = suggest_employers(
            self.user, self.profile, provider=provider2, force=True
        )
        self.assertEqual(candidates[0].display_name, "Second Co")
        self.assertEqual(len(provider2.calls), 1)

    def test_force_refresh_limited_to_once_per_day(self):
        provider = self._provider(["First Co"])
        suggest_employers(self.user, self.profile, provider=provider, force=True)
        provider2 = self._provider(["Second Co"])
        with self.assertRaises(SuggestionError) as ctx:
            suggest_employers(
                self.user, self.profile, provider=provider2, force=True
            )
        self.assertIn("once per day", str(ctx.exception).lower())

    def test_hostile_response_invalid_json(self):
        provider = FakeProvider(response_data={})
        # Force content to invalid JSON by patching the returned content.
        original_complete = provider.complete

        def bad_complete(**kwargs):
            result = original_complete(**kwargs)
            result["content"] = "not json"
            return result

        provider.complete = bad_complete
        with self.assertRaises(SuggestionError):
            suggest_employers(self.user, self.profile, provider=provider)

    def test_hostile_response_invalid_shape(self):
        provider = FakeProvider(
            response_data={"employers": ["a" * 101]},  # exceeds 100 char cap
        )
        with self.assertRaises(SuggestionError):
            suggest_employers(self.user, self.profile, provider=provider)

    @override_settings(KIMI_MAX_CALLS_PER_DAY=1)
    def test_shared_call_cap_blocks_suggestion_after_cv_parse(self):
        LLMUsageLog.objects.create(
            user=self.user,
            model='kimi-k2.6',
            input_tokens=10,
            output_tokens=5,
        )
        provider = self._provider(["Over Budget Co"])
        with self.assertRaises(SuggestionError) as ctx:
            suggest_employers(self.user, self.profile, provider=provider)
        self.assertIn("limit reached", str(ctx.exception).lower())

    def test_two_user_isolation(self):
        other = User.objects.create_user(username='other', password='pass')
        other_profile, _ = UserProfile.objects.get_or_create(
            user=other,
            defaults={
                'cv_parsing_consent': True,
                'parsed_profile': {
                    'skills': ['Cooking'],
                    'preferences': {'job_titles': ['Chef']},
                },
            },
        )
        provider = self._provider(["User Co"])
        suggest_employers(self.user, self.profile, provider=provider)
        self.assertEqual(EmployerCandidate.objects.filter(user=other).count(), 0)
        provider2 = self._provider(["Other Co"])
        suggest_employers(other, other_profile, provider=provider2)
        self.assertEqual(
            EmployerCandidate.objects.filter(user=self.user).count(), 1
        )
        self.assertEqual(
            EmployerCandidate.objects.filter(user=other).count(), 1
        )


class SuggestionPromptTests(TestCase):
    def test_prompt_inputs_exclude_pii_fields(self):
        user = User.objects.create_user(username='promptuser', password='pass')
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                'cv_parsing_consent': True,
                'parsed_profile': {
                    'headline': 'Analyst',
                    'skills': ['Python'],
                    'preferences': {'locations': ['Kenya']},
                    'experience': [{'title': 'Data Analyst'}],
                },
            },
        )
        inputs = _build_prompt_inputs(profile)
        self.assertIn('skills', inputs)
        self.assertIn('countries', inputs)
        self.assertNotIn('name', inputs)
        self.assertNotIn('email', inputs)
        self.assertNotIn('phone', inputs)


class SuggestionEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='apiuser', password='pass')
        self.client.force_authenticate(user=self.user)
        self.profile, _ = UserProfile.objects.get_or_create(
            user=self.user,
            defaults={
                'cv_parsing_consent': True,
                'parsed_profile': {
                    'skills': ['Python'],
                    'preferences': {'job_titles': ['Engineer'], 'locations': ['Kenya']},
                },
            },
        )
        cache.clear()

    def test_suggest_endpoint_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.post('/api/employer-candidates/suggest/')
        self.assertEqual(response.status_code, 403)

    def test_suggest_endpoint_returns_candidates(self):
        provider = FakeProvider(
            response_data={"employers": ["Example Co", "Another Inc"]}
        )
        # Default provider would fail without API key, so inject fake via patch.
        with patch(
            'core.discovery.suggest.get_llm_provider', return_value=provider
        ):
            response = self.client.post(
                '/api/employer-candidates/suggest/',
                {},
                format='json',
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_suggest_endpoint_reports_error(self):
        with patch(
            'core.discovery.suggest.get_llm_provider',
            side_effect=LLMError("no provider"),
        ):
            response = self.client.post(
                '/api/employer-candidates/suggest/',
                {},
                format='json',
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("no provider", response.data["detail"])


class NormalizationTests(TestCase):
    def test_normalize_name_lowercase_alphanumeric(self):
        self.assertEqual(_normalize_name("Example Co."), "exampleco")
        self.assertEqual(_normalize_name("ACME & Sons"), "acmesons")
        self.assertEqual(_normalize_name("  Spaces  "), "spaces")
