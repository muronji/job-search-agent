"""Tests for the SSRF-safe fetch_url wrapper and politeness controls."""
import gzip
import io
import threading
import zlib
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from django.test import TestCase

from core.utils.http import (
    DomainRateLimiter,
    FetchedResponse,
    FetchError,
    RateLimitError,
    can_fetch,
    fetch_url,
)
from core.utils.ssrf import SSRFError, SafeHTTPAdapter, validate_url


class _FakeResponse:
    """Minimal requests Response stand-in for mocked SafeHTTPAdapter.send."""

    def __init__(self, url, status_code, headers=None, content=b""):
        self.url = url
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.text = content.decode("utf-8", errors="replace")
        self.is_redirect = (
            300 <= status_code < 400 and "location" in self.headers
        )
        self.history = []
        self.raw = type(
            "Raw",
            (),
            {"read": lambda _self, _n, decode_content=False: content},
        )()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def raise_for_status(self):
        if self.status_code >= 400:
            from requests import HTTPError

            raise HTTPError(f"{self.status_code}", response=self)


def _fake_redirect_send(request, validate=None, **kwargs):
    """Return a 302 to /redirected, then a 200 from that path.

    If *validate* is provided, it is called with each URL the adapter is
    asked to send, mirroring the production ``send`` method's first step.
    """
    if validate is not None:
        validate(request.url)
    if request.url == "http://public.example.com/":
        resp = _FakeResponse(
            url=request.url,
            status_code=302,
            headers={"location": "http://public.example.com/redirected"},
        )
    else:
        resp = _FakeResponse(
            url="http://public.example.com/redirected", status_code=200
        )
    resp.request = request
    return resp


class DomainRateLimiterTests(TestCase):
    def test_allows_first_request(self):
        limiter = DomainRateLimiter(min_interval=1.0)
        limiter.check("example.com")

    def test_blocks_immediate_second_request(self):
        limiter = DomainRateLimiter(min_interval=1.0)
        limiter.check("example.com")
        with self.assertRaises(RateLimitError):
            limiter.check("example.com")

    def test_allows_after_interval(self):
        limiter = DomainRateLimiter(min_interval=0.01)
        limiter.check("example.com")
        import time
        time.sleep(0.02)
        limiter.check("example.com")

    def test_tracks_domains_independently(self):
        limiter = DomainRateLimiter(min_interval=1.0)
        limiter.check("example.com")
        limiter.check("example.org")


class FetchUrlPolicyTests(TestCase):
    def test_rejects_loopback_url(self):
        with self.assertRaises(SSRFError):
            fetch_url("http://127.0.0.1/", timeout=1)

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(SSRFError):
            fetch_url("ftp://example.com/", timeout=1)


class FetchUrlMockedTests(TestCase):
    @patch("core.utils.http.requests.Session")
    def test_sets_honest_user_agent(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        fake_resp = session.request.return_value.__enter__.return_value
        fake_resp.url = "http://public.example.com/"
        fake_resp.status_code = 200
        fake_resp.headers = {}
        fake_resp.raw.read.return_value = b"ok"
        fetch_url(
            "http://public.example.com/",
            timeout=1,
            rate_limiter=DomainRateLimiter(),
        )
        self.assertIn("User-Agent", session.headers)
        self.assertTrue(session.headers["User-Agent"].startswith("job-search-agent/"))

    @patch("core.utils.http.requests.Session")
    def test_enforces_response_size_cap(self, mock_session_cls):
        fake_resp = mock_session_cls.return_value.request.return_value.__enter__.return_value
        fake_resp.url = "http://public.example.com/big"
        fake_resp.status_code = 200
        fake_resp.headers = {}
        fake_resp.raw.read.return_value = b"x" * 101
        with self.assertRaises(Exception):
            fetch_url(
                "http://public.example.com/big",
                max_size=100,
                timeout=1,
                rate_limiter=DomainRateLimiter(),
            )


class RobotsTxtTests(TestCase):
    @patch("core.utils.http.fetch_url")
    def test_allows_public_path(self, mock_fetch_url):
        mock_fetch_url.return_value = FetchedResponse(
            url="http://example.com/robots.txt",
            status_code=200,
            headers={},
            content=b"User-agent: *\nDisallow: /private/\n",
            text="User-agent: *\nDisallow: /private/\n",
        )
        self.assertTrue(can_fetch("http://example.com/public-job/"))

    @patch("core.utils.http.fetch_url")
    def test_disallows_private_path(self, mock_fetch_url):
        mock_fetch_url.return_value = FetchedResponse(
            url="http://example.com/robots.txt",
            status_code=200,
            headers={},
            content=b"User-agent: *\nDisallow: /private/\n",
            text="User-agent: *\nDisallow: /private/\n",
        )
        self.assertFalse(can_fetch("http://example.com/private/secret-job/"))

    @patch("core.utils.http.fetch_url")
    def test_allows_when_robots_txt_returns_404(self, mock_fetch_url):
        mock_fetch_url.side_effect = FetchError("404 not found")
        mock_fetch_url.side_effect.__cause__ = None
        # Simulate a wrapped requests HTTPError with a 4xx response.
        from requests import HTTPError
        err = FetchError("404 not found")
        err.__cause__ = HTTPError("404")
        err.__cause__.response = type("Response", (), {"status_code": 404})()
        mock_fetch_url.side_effect = err
        self.assertTrue(can_fetch("http://example.com/public-job/"))

    @patch("core.utils.http.fetch_url")
    def test_disallows_when_robots_txt_fails(self, mock_fetch_url):
        mock_fetch_url.side_effect = FetchError("network error")
        self.assertFalse(can_fetch("http://example.com/public-job/"))

    @patch("core.utils.http.fetch_url")
    def test_disallows_when_robots_txt_returns_500(self, mock_fetch_url):
        from requests import HTTPError
        err = FetchError("500 server error")
        err.__cause__ = HTTPError("500")
        err.__cause__.response = type("Response", (), {"status_code": 500})()
        mock_fetch_url.side_effect = err
        self.assertFalse(can_fetch("http://example.com/public-job/"))


class RedirectValidationTests(TestCase):
    def test_redirect_revalidates_destination(self):
        calls = []
        original_validate = validate_url

        def tracking_validate(url):
            calls.append(url)
            original_validate(url)

        with patch(
            "core.utils.ssrf.validate_url", side_effect=tracking_validate
        ) as mock_validate:
            with patch.object(
                SafeHTTPAdapter,
                "send",
                side_effect=lambda req, **kw: _fake_redirect_send(
                    req, validate=mock_validate, **kw
                ),
            ):
                resp = fetch_url(
                    "http://public.example.com/",
                    timeout=1,
                    rate_limiter=DomainRateLimiter(),
                )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.url, "http://public.example.com/redirected")
        self.assertEqual(
            calls,
            [
                "http://public.example.com/",
                "http://public.example.com/redirected",
            ],
        )


class ModuleRateLimiterTests(TestCase):
    def setUp(self):
        from core.utils.http import _default_rate_limiter

        _default_rate_limiter.reset()

    def tearDown(self):
        from core.utils.http import _default_rate_limiter

        _default_rate_limiter.reset()

    @patch("core.utils.http.requests.Session")
    def test_default_limiter_throttles_repeated_calls(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        fake_resp = session.request.return_value.__enter__.return_value
        fake_resp.url = "http://example.com/"
        fake_resp.status_code = 200
        fake_resp.headers = {}
        fake_resp.raw.read.return_value = b"ok"

        fetch_url("http://example.com/", timeout=1)
        with self.assertRaises(RateLimitError):
            fetch_url("http://example.com/", timeout=1)


class FetchUrlWaitTests(TestCase):
    """Tests for the wait-and-retry behaviour of fetch_url."""

    def setUp(self):
        from core.utils.http import _default_rate_limiter

        _default_rate_limiter.reset()

    def tearDown(self):
        from core.utils.http import _default_rate_limiter

        _default_rate_limiter.reset()

    def _mock_session(self, mock_session_cls):
        session = mock_session_cls.return_value
        session.headers = {}
        fake_resp = session.request.return_value.__enter__.return_value
        fake_resp.url = "http://example.com/"
        fake_resp.status_code = 200
        fake_resp.headers = {}
        fake_resp.raw.read.return_value = b"ok"
        return session

    @patch("core.utils.http.time.sleep")
    @patch("core.utils.http.time.monotonic")
    @patch("core.utils.http.requests.Session")
    def test_wait_sleeps_and_retries_short_delay(
        self, mock_session_cls, mock_monotonic, mock_sleep
    ):
        self._mock_session(mock_session_cls)
        clock = {"now": 0.0}

        def tick():
            return clock["now"]

        def doze(seconds):
            clock["now"] += seconds

        mock_monotonic.side_effect = tick
        mock_sleep.side_effect = doze

        fetch_url("http://example.com/", timeout=1, wait=True)
        # Second call arrives before the 1-second interval has elapsed.
        clock["now"] = 0.3
        fetch_url("http://example.com/", timeout=1, wait=True)

        mock_sleep.assert_called_once()
        self.assertAlmostEqual(mock_sleep.call_args[0][0], 0.7, places=5)

    @patch("core.utils.http.time.sleep")
    @patch("core.utils.http.time.monotonic")
    @patch("core.utils.http.requests.Session")
    def test_wait_respects_max_wait_cap(
        self, mock_session_cls, mock_monotonic, mock_sleep
    ):
        self._mock_session(mock_session_cls)
        clock = {"now": 0.0}
        mock_monotonic.side_effect = lambda: clock["now"]
        mock_sleep.side_effect = lambda seconds: clock.__setitem__("now", clock["now"] + seconds)

        fetch_url("http://example.com/", timeout=1, wait=True)
        clock["now"] = 0.1
        with self.assertRaises(RateLimitError):
            fetch_url("http://example.com/", timeout=1, wait=True, max_wait=0.05)

        mock_sleep.assert_not_called()

    @patch("core.utils.http.requests.Session")
    def test_budget_exhausted_is_not_auto_retried(self, mock_session_cls):
        from core.services.scan import BudgetLimiter
        from core.utils.http import BudgetExhaustedError

        self._mock_session(mock_session_cls)
        limiter = BudgetLimiter(max_requests=1)
        fetch_url("http://example.com/", timeout=1, rate_limiter=limiter, wait=True)
        with self.assertRaises(BudgetExhaustedError):
            fetch_url("http://example.com/", timeout=1, rate_limiter=limiter, wait=True)


class FetchedResponseTests(TestCase):
    def test_json_parses_text(self):
        resp = FetchedResponse(
            url="http://example.com/",
            status_code=200,
            headers={},
            content=b'{"jobs": 1}',
            text='{"jobs": 1}',
        )
        self.assertEqual(resp.json(), {"jobs": 1})


class CompressionHandler(BaseHTTPRequestHandler):
    """Serve JSON with configurable Content-Encoding for fetch_url tests."""

    ENCODING = None
    BODY = b'{"jobs": []}'

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if self.ENCODING:
            self.send_header("Content-Encoding", self.ENCODING)
        self.end_headers()
        self.wfile.write(self.BODY)


@contextmanager
def _local_server(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()


class ContentEncodingTests(TestCase):
    """Regression tests for gzip/deflate handling in fetch_url."""

    def setUp(self):
        from core.utils.http import _default_rate_limiter

        _default_rate_limiter.reset()
        # Allow loopback for local test server only.
        self._orig_adapter = SafeHTTPAdapter._ssrf_allow_loopback
        from core.utils.ssrf import SafeHTTPConnection, SafeHTTPSConnection

        self._orig_conn = SafeHTTPConnection._ssrf_allow_loopback
        SafeHTTPAdapter._ssrf_allow_loopback = True
        SafeHTTPConnection._ssrf_allow_loopback = True
        SafeHTTPSConnection._ssrf_allow_loopback = True
        # fetch_url and the adapter both call validate_url; bypass it for loopback tests.
        self._validate_patch_http = patch("core.utils.http.validate_url")
        self._validate_patch_ssrf = patch("core.utils.ssrf.validate_url")
        self._validate_patch_http.start()
        self._validate_patch_ssrf.start()

    def tearDown(self):
        from core.utils.http import _default_rate_limiter

        _default_rate_limiter.reset()
        SafeHTTPAdapter._ssrf_allow_loopback = self._orig_adapter
        from core.utils.ssrf import SafeHTTPConnection, SafeHTTPSConnection

        SafeHTTPConnection._ssrf_allow_loopback = self._orig_conn
        SafeHTTPSConnection._ssrf_allow_loopback = self._orig_conn
        self._validate_patch_http.stop()
        self._validate_patch_ssrf.stop()

    def test_gzip_large_json_decompresses_and_parses(self):
        large_json = b'{"jobs": [' + b'"x",' * 120000 + b'"x"]}'
        compressed = gzip.compress(large_json)
        # Verify the decompressed payload is in the ~450 KB range.
        self.assertGreater(len(large_json), 400 * 1024)
        self.assertLess(len(large_json), 600 * 1024)

        class Handler(CompressionHandler):
            ENCODING = "gzip"
            BODY = compressed

        with _local_server(Handler) as port:
            resp = fetch_url(f"http://127.0.0.1:{port}/", timeout=5)
        self.assertEqual(resp.json(), {"jobs": ["x"] * 120001})

    def test_gzip_compression_bomb_rejected(self):
        bomb = b"A" * (6 * 1024 * 1024)
        compressed = gzip.compress(bomb)

        class Handler(CompressionHandler):
            ENCODING = "gzip"
            BODY = compressed

        with _local_server(Handler) as port:
            with self.assertRaises(FetchError) as ctx:
                fetch_url(f"http://127.0.0.1:{port}/", timeout=5)
        self.assertIn("exceeds", str(ctx.exception).lower())

    def test_deflate_decompresses(self):
        data = b'{"hello": "world"}'
        compressed = zlib.compress(data)

        class Handler(CompressionHandler):
            ENCODING = "deflate"
            BODY = compressed

        with _local_server(Handler) as port:
            resp = fetch_url(f"http://127.0.0.1:{port}/", timeout=5)
        self.assertEqual(resp.json(), {"hello": "world"})

    def test_uncompressed_still_works(self):
        class Handler(CompressionHandler):
            BODY = b'{"plain": true}'

        with _local_server(Handler) as port:
            resp = fetch_url(f"http://127.0.0.1:{port}/", timeout=5)
        self.assertEqual(resp.json(), {"plain": True})

    def test_unsupported_encoding_reported(self):
        class Handler(CompressionHandler):
            ENCODING = "br"
            BODY = b"compressed"

        with _local_server(Handler) as port:
            with self.assertRaises(FetchError) as ctx:
                fetch_url(f"http://127.0.0.1:{port}/", timeout=5)
        self.assertIn("Unsupported Content-Encoding: br", str(ctx.exception))
