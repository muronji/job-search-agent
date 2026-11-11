from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import Job, JobSource, UserJob, UserPreference, UserProfile
from .views import (
    _filter_signature,
    _jobs_data_version,
    _profile_hash,
    _search_cache_key,
)


class AuthenticatedAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass',
        )
        self.client.force_authenticate(user=self.user)


class AdminAPITestCase(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = User.objects.create_user(
            username='adminuser',
            password='adminpass',
            is_staff=True,
        )
        self.client.force_authenticate(user=self.admin_user)


class HealthCheckTests(TestCase):
    def test_health_endpoint_returns_ok(self):
        response = self.client.get('/api/health/')
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'status': 'ok'})


class JobTests(AdminAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = JobSource.objects.create(name='LinkedIn')

    def test_create_job_with_all_fields(self):
        payload = {
            'title': 'Software Engineer',
            'company': 'Acme Corp',
            'description': 'Build things.',
            'location': 'Remote',
            'industry': 'Technology',
            'source': self.source.id,
            'source_url': 'https://example.com/job/1',
            'external_id': 'abc123',
            'employment_type': 'full_time',
            'work_arrangement': 'remote',
            'seniority': 'senior',
            'salary_min': 100000,
            'salary_max': 150000,
            'salary_currency': 'USD',
            'salary_period': 'yearly',
            'application_status': 'saved',
        }
        response = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['title'], 'Software Engineer')
        self.assertEqual(response.data['external_id'], 'abc123')

    def test_create_job_with_required_fields_only(self):
        payload = {
            'title': 'Barista',
            'company': 'Coffee Shop',
        }
        response = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['title'], 'Barista')
        self.assertEqual(response.data['company'], 'Coffee Shop')
        self.assertEqual(response.data['application_status'], 'saved')
        self.assertIsNone(response.data['external_id'])

    def test_missing_title_returns_400(self):
        response = self.client.post('/api/jobs/', {'company': 'X'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('title', response.data)

    def test_invalid_choice_returns_400(self):
        payload = {
            'title': 'Job',
            'company': 'Co',
            'employment_type': 'not_a_choice',
        }
        response = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('employment_type', response.data)

    def test_invalid_url_returns_400(self):
        payload = {
            'title': 'Job',
            'company': 'Co',
            'source_url': 'not-a-url',
        }
        response = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('source_url', response.data)

    def test_salary_min_greater_than_max_returns_400(self):
        payload = {
            'title': 'Job',
            'company': 'Co',
            'salary_min': 200000,
            'salary_max': 100000,
        }
        response = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('salary_min', response.data)

    def test_retrieve_job(self):
        job = Job.objects.create(title='Nurse', company='Hospital')
        response = self.client.get(f'/api/jobs/{job.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['title'], 'Nurse')

    def test_update_status_via_patch(self):
        job = Job.objects.create(title='Teacher', company='School')
        response = self.client.patch(
            f'/api/jobs/{job.id}/',
            {'application_status': 'applied'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['application_status'], 'applied')

    def test_delete_job(self):
        job = Job.objects.create(title='Chef', company='Restaurant')
        response = self.client.delete(f'/api/jobs/{job.id}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Job.objects.filter(id=job.id).exists())


class JobFilteringTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        self.remote_job = Job.objects.create(
            title='Remote Developer',
            company='Tech Inc',
            location='Remote',
            industry='Technology',
            employment_type='full_time',
            application_status='saved',
        )
        self.local_job = Job.objects.create(
            title='Local Nurse',
            company='Health Corp',
            location='New York, NY',
            industry='Healthcare',
            employment_type='full_time',
            application_status='applied',
        )

    def test_list_jobs_no_filter(self):
        response = self.client.get('/api/jobs/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)

    def test_filter_by_application_status(self):
        response = self.client.get('/api/jobs/?application_status=applied')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Local Nurse')

    def test_filter_by_location(self):
        response = self.client.get('/api/jobs/?location=remote')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Remote Developer')

    def test_filter_by_industry(self):
        response = self.client.get('/api/jobs/?industry=healthcare')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Local Nurse')

    def test_search_by_keyword(self):
        response = self.client.get('/api/jobs/?search=Developer')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Remote Developer')


class ExternalIdTests(AdminAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = JobSource.objects.create(name='Indeed')

    def test_duplicate_external_id_for_same_source_fails(self):
        payload = {
            'title': 'Job One',
            'company': 'Co',
            'source': self.source.id,
            'external_id': 'dup123',
        }
        first = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(first.status_code, 201)

        second = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(second.status_code, 400)

    def test_blank_external_id_allowed_multiple_times(self):
        payload = {
            'title': 'Job',
            'company': 'Co',
            'source': self.source.id,
            'external_id': '',
        }
        first = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertIsNone(first.data['external_id'])

        second = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(second.status_code, 201)

    def test_whitespace_external_id_normalized_to_null(self):
        payload = {
            'title': 'Job',
            'company': 'Co',
            'source': self.source.id,
            'external_id': '   ',
        }
        response = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data['external_id'])

    def test_missing_external_id_allowed(self):
        payload = {
            'title': 'Job',
            'company': 'Co',
            'source': self.source.id,
        }
        response = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data['external_id'])

    def test_same_external_id_on_different_sources_allowed(self):
        other_source = JobSource.objects.create(name='LinkedIn')
        payload = {
            'title': 'Job',
            'company': 'Co',
            'source': self.source.id,
            'external_id': 'shared-id',
        }
        first = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(first.status_code, 201)

        payload['source'] = other_source.id
        second = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(second.status_code, 201)

    def test_manual_job_without_source_or_external_id(self):
        payload = {
            'title': 'Job',
            'company': 'Co',
        }
        response = self.client.post('/api/jobs/', payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data['source'])
        self.assertIsNone(response.data['external_id'])


class SalaryFilterTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        self.paid_job = Job.objects.create(
            title='Paid Job',
            company='Co',
            salary_min=80000,
            salary_max=120000,
        )
        self.unpaid_job = Job.objects.create(
            title='Unpaid Intern',
            company='Org',
        )

    def test_salary_filter_excludes_jobs_without_salary(self):
        response = self.client.get('/api/jobs/?salary_min=50000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Paid Job')

    def test_salary_filter_includes_overlapping_range(self):
        response = self.client.get('/api/jobs/?salary_min=90000&salary_max=100000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Paid Job')

    def test_salary_filter_excludes_non_overlapping_range(self):
        response = self.client.get('/api/jobs/?salary_min=130000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)


class JobSourceTests(AdminAPITestCase):
    def test_create_source(self):
        response = self.client.post('/api/sources/', {'name': 'LinkedIn'}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['name'], 'LinkedIn')

    def test_list_sources(self):
        JobSource.objects.create(name='Indeed')
        response = self.client.get('/api/sources/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

    def test_update_source(self):
        source = JobSource.objects.create(name='Old Name')
        response = self.client.patch(
            f'/api/sources/{source.id}/',
            {'name': 'New Name'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['name'], 'New Name')


class JobWritePermissionTests(AuthenticatedAPITestCase):
    def test_non_admin_cannot_create_job(self):
        response = self.client.post('/api/jobs/', {'title': 'Job', 'company': 'Co'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_non_admin_cannot_update_job(self):
        job = Job.objects.create(title='Teacher', company='School')
        response = self.client.patch(
            f'/api/jobs/{job.id}/',
            {'application_status': 'applied'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)

    def test_non_admin_cannot_delete_job(self):
        job = Job.objects.create(title='Chef', company='Restaurant')
        response = self.client.delete(f'/api/jobs/{job.id}/')
        self.assertEqual(response.status_code, 403)

    def test_authenticated_user_can_list_jobs(self):
        Job.objects.create(title='Nurse', company='Hospital')
        response = self.client.get('/api/jobs/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)


class JobSourceWritePermissionTests(AuthenticatedAPITestCase):
    def test_non_admin_cannot_create_source(self):
        response = self.client.post('/api/sources/', {'name': 'LinkedIn'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_non_admin_cannot_update_source(self):
        source = JobSource.objects.create(name='Old Name')
        response = self.client.patch(
            f'/api/sources/{source.id}/',
            {'name': 'New Name'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)

    def test_authenticated_user_can_list_sources(self):
        JobSource.objects.create(name='Indeed')
        response = self.client.get('/api/sources/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)


class UserPreferenceTests(AuthenticatedAPITestCase):
    def test_retrieve_preferences_creates_record_for_user(self):
        self.assertEqual(UserPreference.objects.count(), 0)
        response = self.client.get('/api/preferences/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserPreference.objects.count(), 1)
        self.assertEqual(response.data['job_titles'], [])
        self.assertEqual(UserPreference.objects.first().user, self.user)

    def test_update_preferences(self):
        self.client.get('/api/preferences/')
        payload = {
            'job_titles': ['software engineer', 'data scientist'],
            'industries': ['technology', 'finance'],
            'locations': ['remote', 'new york'],
            'seniority_levels': ['senior'],
            'employment_types': ['full_time'],
            'work_arrangements': ['remote', 'hybrid'],
            'min_salary': 100000,
        }
        response = self.client.put('/api/preferences/', payload, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['job_titles'], ['software engineer', 'data scientist'])
        self.assertEqual(response.data['min_salary'], 100000)


class AuthenticationTests(APITestCase):
    def test_health_endpoint_allows_anonymous(self):
        response = self.client.get('/api/health/')
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'status': 'ok'})

    def test_other_endpoints_require_authentication(self):
        response = self.client.get('/api/jobs/')
        self.assertEqual(response.status_code, 403)


class TokenAuthenticationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='tokenuser',
            password='tokenpass',
        )

    def test_token_login_returns_token(self):
        response = self.client.post(
            '/api/auth/token/',
            {'username': 'tokenuser', 'password': 'tokenpass'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('token', response.data)

    def test_token_authenticates_subsequent_requests(self):
        from rest_framework.authtoken.models import Token
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        response = self.client.get('/api/jobs/')
        self.assertEqual(response.status_code, 200)


class ThrottlingTests(AuthenticatedAPITestCase):
    def test_user_throttle_limits_requests(self):
        from rest_framework.throttling import UserRateThrottle

        original_rates = UserRateThrottle.THROTTLE_RATES
        UserRateThrottle.THROTTLE_RATES = {'user': '1/minute'}
        cache.clear()
        try:
            response = self.client.get('/api/jobs/')
            self.assertEqual(response.status_code, 200)
            response = self.client.get('/api/jobs/')
            self.assertEqual(response.status_code, 429)
        finally:
            UserRateThrottle.THROTTLE_RATES = original_rates


class UserJobTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        self.job = Job.objects.create(title='Nurse', company='Hospital')

    def test_create_user_job(self):
        response = self.client.post(
            '/api/user-jobs/',
            {'job': self.job.id, 'application_status': 'applied'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['job'], self.job.id)
        self.assertEqual(response.data['application_status'], 'applied')
        self.assertEqual(UserJob.objects.first().user, self.user)

    def test_user_job_isolation(self):
        other_user = User.objects.create_user(
            username='otheruser',
            password='otherpass',
        )
        UserJob.objects.create(user=other_user, job=self.job, application_status='saved')
        UserJob.objects.create(user=self.user, job=self.job, application_status='applied')

        response = self.client.get('/api/user-jobs/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['application_status'], 'applied')

    def test_duplicate_user_job_rejected(self):
        UserJob.objects.create(user=self.user, job=self.job, application_status='saved')
        response = self.client.post(
            '/api/user-jobs/',
            {'job': self.job.id, 'application_status': 'applied'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)


class SearchVerdictCountsTests(AuthenticatedAPITestCase):
    def test_verdict_counts_not_scored_without_profile(self):
        Job.objects.create(title='Engineer', company='A')
        Job.objects.create(title='Designer', company='B')
        Job.objects.create(title='Manager', company='C')

        response = self.client.get('/api/search/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('verdict_counts', response.data)
        counts = response.data['verdict_counts']
        self.assertEqual(counts['not_scored'], 3)
        self.assertEqual(counts['apply'], 0)
        self.assertEqual(counts['track'], 0)
        self.assertEqual(counts['ignore'], 0)

    def test_verdict_counts_with_profile(self):
        UserProfile.objects.create(
            user=self.user,
            parsed_profile={
                'skills': ['python'],
                'preferences': {
                    'job_titles': ['Engineer'],
                    'locations': ['Kenya'],
                },
            },
        )
        Job.objects.create(title='Software Engineer', company='A', location='Nairobi, Kenya')
        Job.objects.create(title='Barista', company='B', location='Nairobi, Kenya')

        response = self.client.get('/api/search/')
        self.assertEqual(response.status_code, 200)
        counts = response.data['verdict_counts']
        self.assertEqual(
            sum(counts.values()),
            response.data['count'],
        )
        self.assertIn(counts['not_scored'], [0, 1, 2])

    def test_verdict_counts_respect_non_verdict_filters(self):
        UserProfile.objects.create(
            user=self.user,
            parsed_profile={
                'skills': ['python'],
                'preferences': {'job_titles': ['Engineer']},
            },
        )
        Job.objects.create(title='Software Engineer', company='A', location='Nairobi, Kenya')
        Job.objects.create(title='Hardware Engineer', company='B', location='London, UK')

        response = self.client.get('/api/search/?country=Kenya')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        counts = response.data['verdict_counts']
        self.assertEqual(sum(counts.values()), 1)


class CountryFilterTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        Job.objects.create(title='UK Job', company='A', location='London, UK')
        Job.objects.create(title='US Job', company='B', location='New York, NY')
        Job.objects.create(title='Remote Job', company='C', work_arrangement='remote')
        Job.objects.create(title='UAE Job', company='D', location='Dubai, UAE')

    def test_country_filter_uk_alias(self):
        response = self.client.get('/api/search/?country=United Kingdom')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'UK Job')

    def test_country_filter_us_alias(self):
        response = self.client.get('/api/search/?country=United States')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'US Job')

    def test_country_filter_uae_alias(self):
        response = self.client.get('/api/search/?country=United Arab Emirates')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'UAE Job')

    def test_country_filter_remote(self):
        response = self.client.get('/api/search/?country=Remote')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Remote Job')

    def test_country_filter_no_match(self):
        response = self.client.get('/api/search/?country=Uganda')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

    def test_country_filter_no_us_false_positive_for_uk(self):
        # "London, UK" must not match United States aliases such as "nd".
        response = self.client.get('/api/search/?country=United States')
        self.assertEqual(response.status_code, 200)
        titles = {r['title'] for r in response.data['results']}
        self.assertNotIn('UK Job', titles)


class SearchCacheTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.profile = UserProfile.objects.create(
            user=self.user,
            parsed_profile={
                'skills': ['python'],
                'location': 'Nairobi, Kenya',
                'preferences': {
                    'job_titles': ['Engineer'],
                    'locations': ['Kenya'],
                },
            },
        )
        self.job = Job.objects.create(
            title='Software Engineer',
            company='A',
            location='Nairobi, Kenya',
            description='Python and Django role.',
        )

    def _search_cache_key_for(self, query=''):
        return _search_cache_key(
            self.user.id,
            _profile_hash(self.user),
            _filter_signature({'q': query} if query else {}, ''),
            _jobs_data_version(),
        )

    def test_search_result_is_cached(self):
        key = self._search_cache_key_for()
        self.assertIsNone(cache.get(key))
        response = self.client.get('/api/search/')
        self.assertEqual(response.status_code, 200)
        cached = cache.get(key)
        self.assertIsNotNone(cached)
        self.assertIn('results', cached)
        self.assertIn('verdict_counts', cached)

    def test_cache_hit_returns_same_response(self):
        first = self.client.get('/api/search/')
        second = self.client.get('/api/search/')
        self.assertEqual(first.data['count'], second.data['count'])
        self.assertEqual(
            [r['id'] for r in first.data['results']],
            [r['id'] for r in second.data['results']],
        )
        self.assertEqual(
            first.data['verdict_counts'],
            second.data['verdict_counts'],
        )

    def test_cache_isolated_per_user(self):
        other = User.objects.create_user(username='othercache', password='otherpass')
        UserProfile.objects.create(
            user=other,
            parsed_profile={
                'skills': ['excel'],
                'preferences': {'job_titles': ['Analyst']},
            },
        )
        key_user = _search_cache_key(
            self.user.id,
            _profile_hash(self.user),
            _filter_signature({}, ''),
            _jobs_data_version(),
        )
        key_other = _search_cache_key(
            other.id,
            _profile_hash(other),
            _filter_signature({}, ''),
            _jobs_data_version(),
        )
        self.assertNotEqual(key_user, key_other)

        self.client.get('/api/search/')
        self.assertIsNotNone(cache.get(key_user))
        self.assertIsNone(cache.get(key_other))

    def test_cache_invalidated_on_profile_change(self):
        first = self.client.get('/api/search/')
        first_key = self._search_cache_key_for()
        self.profile.parsed_profile = {
            'skills': ['cooking'],
            'preferences': {
                'job_titles': ['Chef'],
                'locations': ['France'],
            },
        }
        self.profile.save()
        second_key = self._search_cache_key_for()
        self.assertNotEqual(first_key, second_key)
        second = self.client.get('/api/search/')
        self.assertNotEqual(
            first.data['verdict_counts'],
            second.data['verdict_counts'],
        )

    def test_cache_invalidated_on_job_change(self):
        first = self.client.get('/api/search/')
        first_key = self._search_cache_key_for()
        Job.objects.create(title='Data Engineer', company='B', location='Remote')
        second_key = self._search_cache_key_for()
        self.assertNotEqual(first_key, second_key)
        second = self.client.get('/api/search/')
        self.assertGreater(second.data['count'], first.data['count'])
