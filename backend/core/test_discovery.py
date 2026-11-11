"""Tests for ATS discovery, probe endpoint, and coverage endpoint."""
import json
from unittest.mock import MagicMock, patch

from core.discovery import probe_employer_name, probe_greenhouse
from core.models import Job, JobSource, UserProfile
from core.tests import AuthenticatedAPITestCase
from core.utils.http import BudgetExhaustedError, FetchError


class MockResponse:
    def __init__(self, data, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


def _http_fetch_error(status_code: int, message: str = "") -> FetchError:
    """Return a FetchError carrying an HTTP status code, as fetch_url produces."""
    from requests import HTTPError

    err = FetchError(message or f"{status_code} Client Error")
    err.__cause__ = HTTPError(f"{status_code} Client Error")
    err.__cause__.response = MagicMock(status_code=status_code)
    return err


class DiscoveryProbeTests(AuthenticatedAPITestCase):
    @patch("core.discovery.fetch_url")
    @patch("core.discovery.can_fetch", return_value=True)
    def test_probe_reports_found_and_not_found(self, mock_can, mock_fetch):
        def side_effect(url, **kwargs):
            if "greenhouse" in url:
                return MockResponse({"jobs": [{"id": 1}]})
            if "lever" in url:
                return MockResponse([{"id": 2}])
            # Ashby returns a 404 -> should be reported as not_found.
            raise _http_fetch_error(404, "404 not found")

        mock_fetch.side_effect = side_effect
        response = self.client.post("/api/discover/probe/", {"name": "Example Co"}, format="json")
        self.assertEqual(response.status_code, 200)
        candidates = response.data["candidates"]
        self.assertEqual(len(candidates), 3)
        statuses = {c["adapter"]: c["status"] for c in candidates}
        self.assertEqual(statuses["greenhouse"], "found")
        self.assertEqual(statuses["lever"], "found")
        self.assertEqual(statuses["ashby"], "not_found")
        found = [c for c in candidates if c["status"] == "found"]
        self.assertTrue(all("seed_entry" in c for c in found))

    @patch("core.discovery.fetch_url")
    @patch("core.discovery.can_fetch", return_value=True)
    def test_probe_returns_empty_when_nothing_found(self, mock_can, mock_fetch):
        mock_fetch.side_effect = lambda url, **kwargs: (_ for _ in ()).throw(
            _http_fetch_error(404, "404 not found")
        )
        response = self.client.post("/api/discover/probe/", {"name": "Bad Co"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(c["status"] == "not_found" for c in response.data["candidates"]))

    @patch("core.discovery.fetch_url")
    @patch("core.discovery.can_fetch", return_value=True)
    def test_probe_reports_forbidden(self, mock_can, mock_fetch):
        def side_effect(url, **kwargs):
            if "greenhouse" in url:
                raise _http_fetch_error(403, "403 Client Error")
            return MockResponse({"jobs": [{"id": 1}]})

        mock_fetch.side_effect = side_effect
        response = self.client.post("/api/discover/probe/", {"name": "Example Co"}, format="json")
        self.assertEqual(response.status_code, 200)
        statuses = {c["adapter"]: c["status"] for c in response.data["candidates"]}
        self.assertEqual(statuses["greenhouse"], "forbidden")

    def test_probe_requires_name(self):
        response = self.client.post("/api/discover/probe/", {}, format="json")
        self.assertEqual(response.status_code, 400)


class CoverageViewTests(AuthenticatedAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = JobSource.objects.create(name="Source")
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={
                "parsed_profile": {
                    "location": "Nairobi",
                    "preferences": {"locations": ["Kenya"]},
                }
            },
        )

    def test_coverage_counts_and_suggests_nearby(self):
        Job.objects.create(
            source=self.source,
            title="Analyst",
            company="Co",
            location="Nairobi, Kenya",
            source_listing_status=Job.SourceListingStatus.ACTIVE,
        )
        response = self.client.get("/api/search/coverage/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["country_counts"].get("Kenya"), 1)
        self.assertIn("Kenya", response.data["suggestions"])
        suggestion = response.data["suggestions"]["Kenya"]
        self.assertIn("Uganda", suggestion)
        self.assertIn("Tanzania", suggestion)

    def test_coverage_counts_multi_location_for_each_country(self):
        Job.objects.create(
            source=self.source,
            title="Global Role",
            company="Co",
            location="New York, NY, London, Toronto",
            source_listing_status=Job.SourceListingStatus.ACTIVE,
        )
        response = self.client.get("/api/search/coverage/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["country_counts"].get("United States"), 1)
        self.assertEqual(response.data["country_counts"].get("United Kingdom"), 1)
        self.assertEqual(response.data["country_counts"].get("Canada"), 1)


class ProbeStatusTests(AuthenticatedAPITestCase):
    @patch("core.discovery.can_fetch", return_value=True)
    def test_probe_404_returns_not_found(self, mock_can):
        with patch("core.discovery.fetch_url", side_effect=_http_fetch_error(404)):
            outcome = probe_greenhouse("exampleco")
        self.assertEqual(outcome.status, "not_found")
        self.assertEqual(outcome.http_status, 404)
        self.assertIn("404", outcome.detail)

    @patch("core.discovery.can_fetch", return_value=True)
    def test_probe_403_returns_forbidden(self, mock_can):
        with patch("core.discovery.fetch_url", side_effect=_http_fetch_error(403)):
            outcome = probe_greenhouse("exampleco")
        self.assertEqual(outcome.status, "forbidden")
        self.assertEqual(outcome.http_status, 403)
        self.assertIn("403", outcome.detail)

    @patch("core.discovery.can_fetch", return_value=False)
    def test_probe_robots_disallowed_returns_forbidden(self, mock_can):
        with patch("core.discovery.fetch_url") as mock_fetch:
            mock_fetch.side_effect = AssertionError("fetch_url should not be called")
            outcome = probe_greenhouse("exampleco")
        self.assertEqual(outcome.status, "forbidden")
        self.assertIn("robots.txt", outcome.detail)

    @patch("core.discovery.can_fetch", return_value=True)
    def test_probe_network_error_returns_error(self, mock_can):
        err = FetchError("connection timeout")
        err.__cause__ = None
        with patch("core.discovery.fetch_url", side_effect=err):
            outcome = probe_greenhouse("exampleco")
        self.assertEqual(outcome.status, "error")
        self.assertIn("fetch failed", outcome.detail)

    @patch("core.discovery.can_fetch", return_value=True)
    def test_probe_response_too_large_returns_error(self, mock_can):
        err = ValueError("Response exceeds 5242880 bytes")
        with patch("core.discovery.fetch_url", side_effect=err):
            outcome = probe_greenhouse("exampleco")
        self.assertEqual(outcome.status, "error")
        self.assertIn("exceeds", outcome.detail.lower())

    @patch("core.discovery.can_fetch", return_value=True)
    def test_probe_bad_json_returns_error(self, mock_can):
        bad_response = MagicMock()
        bad_response.status_code = 200
        bad_response.json.side_effect = json.JSONDecodeError("err", "not json", 0)
        with patch("core.discovery.fetch_url", return_value=bad_response):
            outcome = probe_greenhouse("exampleco")
        self.assertEqual(outcome.status, "error")
        self.assertIn("invalid JSON", outcome.detail)

    @patch("core.discovery.can_fetch", return_value=True)
    def test_probe_rate_limit_exhausted_returns_error(self, mock_can):
        err = BudgetExhaustedError("budget exhausted")
        with patch("core.discovery.fetch_url", side_effect=err):
            outcome = probe_greenhouse("exampleco")
        self.assertEqual(outcome.status, "error")
        self.assertIn("budget", outcome.detail.lower())

    @patch("core.discovery.can_fetch", return_value=True)
    def test_probe_large_board_returns_found_with_count(self, mock_can):
        large_payload = {
            "jobs": [
                {"id": i, "title": f"Engineer {i}"}
                for i in range(500)
            ]
        }
        with patch("core.discovery.fetch_url", return_value=MockResponse(large_payload)):
            outcome = probe_greenhouse("bigco")
        self.assertEqual(outcome.status, "found")
        self.assertEqual(outcome.result.job_count, 500)


class ProbeRateLimiterTests(AuthenticatedAPITestCase):
    """Exercise probe_employer_name with the real module-level rate limiter."""

    def setUp(self):
        super().setUp()
        from core.utils.http import _default_rate_limiter

        _default_rate_limiter.reset()

    def tearDown(self):
        from core.utils.http import _default_rate_limiter

        _default_rate_limiter.reset()
        super().tearDown()

    @patch("core.utils.http.requests.Session")
    @patch("core.utils.http.time.sleep")
    @patch("core.utils.http.time.monotonic")
    def test_probe_employer_name_waits_for_real_rate_limiter(
        self, mock_monotonic, mock_sleep, mock_session_cls
    ):
        """Back-to-back probes to the same domain should sleep and retry."""
        clock = {"now": 0.0}

        def tick():
            return clock["now"]

        def doze(seconds):
            clock["now"] += seconds

        mock_monotonic.side_effect = tick
        mock_sleep.side_effect = doze

        class FakeResponse:
            def __init__(self, url, text):
                self.url = url
                self.status_code = 200
                self.headers = {}
                self.text = text
                self.content = text.encode("utf-8")
                self.raw = type(
                    "Raw",
                    (),
                    {"read": lambda _self, n, decode_content=False: self.content[:n]},
                )()
                self.is_redirect = False
                self.history = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def raise_for_status(self):
                pass

        def fake_request(method, url, **kwargs):
            if "/robots.txt" in url:
                text = "User-agent: *\nDisallow:\n"
            elif "greenhouse" in url:
                text = '{"jobs": [{"id": 1}]}'
            elif "lever" in url:
                text = '[{"id": 2}]'
            elif "ashby" in url:
                text = '{"jobs": [{"id": 3}]}'
            else:
                text = ""
            return FakeResponse(url, text)

        session = MagicMock()
        session.headers = {}
        session.request = fake_request
        session.mount = MagicMock()
        session.close = MagicMock()
        mock_session_cls.return_value = session

        outcomes = probe_employer_name("Example Co")

        statuses = {o.adapter_key: o.status for o in outcomes}
        self.assertEqual(statuses["greenhouse"], "found")
        self.assertEqual(statuses["lever"], "found")
        self.assertEqual(statuses["ashby"], "found")
        # Each probe makes two calls to the same domain (robots.txt + API).
        # The second call should have triggered at least one wait.
        self.assertGreaterEqual(mock_sleep.call_count, 1)
