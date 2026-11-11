"""Tests for Phase 3 ingestion, SSRF protection, adapters, and normalization."""
import csv
import hashlib
import json
import os
import socket
import ssl
import tempfile
import urllib3
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO, StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APITestCase

from core.adapters.base import BaseAdapter
from core.adapters.impl.file_adapters import CsvAdapter, JsonAdapter
from core.adapters.impl.ashby import AshbyAdapter
from core.adapters.impl.greenhouse import GreenhouseAdapter
from core.adapters.impl.lever import LeverAdapter
from core.adapters.impl.remoteok import RemoteOkAdapter
from core.adapters.impl.rss import RssAdapter
from core.adapters.impl.usajobs import UsaJobsAdapter
from core.adapters.normalizers import (
    normalize_date,
    normalize_employment_type,
    normalize_external_id,
    normalize_salary,
    normalize_seniority,
    normalize_text,
    normalize_url,
    strip_html,
)
from core.adapters.registry import get_adapter_class
from core.models import Job, JobSource
from core.tests import AuthenticatedAPITestCase
from core.services.ingestion import IngestionService
from core.utils.http import FetchedResponse, fetch_url
from core.utils.secrets import normalize_key, scan_for_secrets
from core.utils.ssrf import (
    SSRFError,
    SafeHTTPAdapter,
    SafeHTTPConnection,
    SafeHTTPSConnection,
    SafePoolManager,
    validate_url,
)


# ---------- Test-only SSRF classes (production code never uses these) ----------

class TestSafeHTTPConnection(SafeHTTPConnection):
    _ssrf_allow_loopback = True


class TestSafeHTTPSConnection(SafeHTTPSConnection):
    _ssrf_allow_loopback = True


class TestSafeHTTPConnectionPool(urllib3_pool := __import__("urllib3").HTTPConnectionPool):
    ConnectionCls = TestSafeHTTPConnection


class TestSafeHTTPSConnectionPool(urllib3s_pool := __import__("urllib3").HTTPSConnectionPool):
    ConnectionCls = TestSafeHTTPSConnection


class TestSafePoolManager(SafePoolManager):
    def _new_pool(self, scheme, host, port, request_context=None):
        if scheme == "http":
            return TestSafeHTTPConnectionPool(host, port, **self.connection_pool_kw)
        if scheme == "https":
            return TestSafeHTTPSConnectionPool(host, port, **self.connection_pool_kw)
        raise SSRFError(f"Scheme not allowed: {scheme}")


class TestSafeHTTPAdapter(SafeHTTPAdapter):
    _ssrf_allow_loopback = True

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self.poolmanager = TestSafePoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )

    def _validate_request_url(self, url: str) -> None:
        # Test-only: skip production URL validation so loopback tests can run.
        pass


# ---------- Local test server helpers ----------

class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path in ("/redirect-to-prohibited", "/redirect-to-prohibited/"):
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.1/")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")


@contextmanager
def local_http_server(https=False, cert_file=None, key_file=None):
    server = HTTPServer(("127.0.0.1", 0), QuietHandler)
    port = server.server_address[1]
    if https:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_file, key_file)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()


# ---------- SSRF production-policy tests ----------

class SSRFProductionPolicyTests(TestCase):
    def test_rejects_loopback_ipv4(self):
        for url in ["http://127.0.0.1/", "http://0177.0.0.01/", "http://0x7f000001/", "http://2130706433/"]:
            with self.subTest(url=url):
                with self.assertRaises(SSRFError):
                    validate_url(url)

    def test_rejects_loopback_ipv6(self):
        for url in ["http://[::1]/", "http://[::ffff:127.0.0.1]/"]:
            with self.subTest(url=url):
                with self.assertRaises(SSRFError):
                    validate_url(url)

    def test_rejects_private_ipv4(self):
        for url in ["http://10.0.0.1/", "http://192.168.1.1/", "http://172.16.0.1/"]:
            with self.subTest(url=url):
                with self.assertRaises(SSRFError):
                    validate_url(url)

    def test_rejects_special_purpose_ips(self):
        urls = [
            "http://100.64.0.1/",          # CGNAT
            "http://192.0.0.1/",           # IETF protocol assignments
            "http://198.18.0.1/",          # Benchmark testing
            "http://[2001:db8::1]/",       # Documentation range
            "http://[ff00::1]/",           # Multicast
            "http://224.0.0.1/",           # Multicast
            "http://[::ffff:10.0.0.1]/",   # IPv4-mapped private
        ]
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(SSRFError):
                    validate_url(url)

    def test_rejects_embedded_credentials(self):
        with self.assertRaises(SSRFError):
            validate_url("http://user:pass@example.com/")

    def test_rejects_non_http_schemes(self):
        with self.assertRaises(SSRFError):
            validate_url("ftp://example.com/")

    def test_accepts_public_url(self):
        validate_url("https://example.com/jobs")

    def test_production_adapter_rejects_loopback_before_socket(self):
        adapter = SafeHTTPAdapter()
        original_getaddrinfo = socket.getaddrinfo
        original_connect = socket.socket.connect
        called = {"getaddrinfo": False, "connect": False}

        def fake_getaddrinfo(*args, **kwargs):
            called["getaddrinfo"] = True
            return original_getaddrinfo(*args, **kwargs)

        def fake_connect(self, address):
            called["connect"] = True
            return original_connect(self, address)

        socket.getaddrinfo = fake_getaddrinfo
        socket.socket.connect = fake_connect
        try:
            import requests
            session = requests.Session()
            session.mount("http://", adapter)
            with self.assertRaises((SSRFError, requests.exceptions.ConnectionError)):
                session.get("http://127.0.0.1/", timeout=1)
            self.assertFalse(called["getaddrinfo"])
            self.assertFalse(called["connect"])
        finally:
            socket.getaddrinfo = original_getaddrinfo
            socket.socket.connect = original_connect

    def test_dns_rebinding_rejected(self):
        adapter = SafeHTTPAdapter()
        original_getaddrinfo = socket.getaddrinfo

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "public.example.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", port))]
            return original_getaddrinfo(host, port, *args, **kwargs)

        socket.getaddrinfo = fake_getaddrinfo
        try:
            import requests
            session = requests.Session()
            session.mount("http://", adapter)
            with self.assertRaises((SSRFError, requests.exceptions.ConnectionError)):
                session.get("http://public.example.com/", timeout=1)
        finally:
            socket.getaddrinfo = original_getaddrinfo


# ---------- SSRF connection-mechanics tests ----------

class SSRFConnectionMechanicsTests(TestCase):
    def test_http_connection_to_localhost_reaches_127_0_0_1(self):
        with local_http_server() as port:
            import requests
            session = requests.Session()
            session.mount("http://", TestSafeHTTPAdapter())
            resp = session.get(f"http://localhost:{port}/", timeout=5)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.text, "ok")

    def test_https_connection_to_localhost_verifies_cert(self):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        crt = os.path.join(fixtures_dir, "test_localhost.crt")
        key = os.path.join(fixtures_dir, "test_localhost.key")
        with local_http_server(https=True, cert_file=crt, key_file=key) as port:
            import requests
            session = requests.Session()
            session.mount("https://", TestSafeHTTPAdapter())
            resp = session.get(
                f"https://localhost:{port}/",
                timeout=5,
                verify=crt,
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.text, "ok")

    def test_redirect_to_prohibited_rejected(self):
        with local_http_server() as port:
            import requests
            session = requests.Session()
            session.mount("http://", TestSafeHTTPAdapter())
            with self.assertRaises((SSRFError, requests.exceptions.ConnectionError)):
                session.get(f"http://localhost:{port}/redirect-to-prohibited/", timeout=5)

    def test_connection_destination_is_validated_ip(self):
        adapter = SafeHTTPAdapter()
        original_getaddrinfo = socket.getaddrinfo
        original_connect = socket.socket.connect
        seen = {}

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "public.example.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))]
            return original_getaddrinfo(host, port, *args, **kwargs)

        def fake_connect(self, address):
            seen["address"] = address
            raise OSError("Intentional abort")

        socket.getaddrinfo = fake_getaddrinfo
        socket.socket.connect = fake_connect
        try:
            import requests
            session = requests.Session()
            session.mount("http://", adapter)
            with self.assertRaises(requests.exceptions.ConnectionError):
                session.get("http://public.example.com/", timeout=1)
            self.assertEqual(seen.get("address"), ("93.184.216.34", 80))
        finally:
            socket.getaddrinfo = original_getaddrinfo
            socket.socket.connect = original_connect

    def test_tls_hostname_is_original_hostname(self):
        adapter = SafeHTTPAdapter()
        original_getaddrinfo = socket.getaddrinfo
        seen_hostname = []

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "public.example.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))]
            return original_getaddrinfo(host, port, *args, **kwargs)

        original_connect = socket.socket.connect

        def fake_connect(self, address):
            # Allow TCP connect to succeed so TLS wrapping is reached.
            pass

        import urllib3.connection as urllib3_connection
        original_ssl_wrap = urllib3_connection._ssl_wrap_socket_and_match_hostname

        def fake_ssl_wrap(*args, server_hostname=None, **kwargs):
            seen_hostname.append(server_hostname)
            raise OSError("Intentional abort")

        socket.getaddrinfo = fake_getaddrinfo
        socket.socket.connect = fake_connect
        urllib3_connection._ssl_wrap_socket_and_match_hostname = fake_ssl_wrap
        try:
            import requests
            session = requests.Session()
            session.mount("https://", adapter)
            with self.assertRaises(requests.exceptions.ConnectionError):
                session.get("https://public.example.com/", timeout=1)
            self.assertIn("public.example.com", seen_hostname)
        finally:
            socket.getaddrinfo = original_getaddrinfo
            socket.socket.connect = original_connect
            urllib3_connection._ssl_wrap_socket_and_match_hostname = original_ssl_wrap

    def test_mixed_dns_answer_uses_only_public_address(self):
        adapter = SafeHTTPAdapter()
        original_getaddrinfo = socket.getaddrinfo
        original_connect = socket.socket.connect
        seen = {}

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "mixed.example.com":
                return [
                    (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", port)),
                    (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port)),
                ]
            return original_getaddrinfo(host, port, *args, **kwargs)

        def fake_connect(self, address):
            seen["address"] = address
            raise OSError("Intentional abort")

        socket.getaddrinfo = fake_getaddrinfo
        socket.socket.connect = fake_connect
        try:
            import requests
            session = requests.Session()
            session.mount("http://", adapter)
            with self.assertRaises(requests.exceptions.ConnectionError):
                session.get("http://mixed.example.com/", timeout=1)
            self.assertEqual(seen.get("address"), ("93.184.216.34", 80))
        finally:
            socket.getaddrinfo = original_getaddrinfo
            socket.socket.connect = original_connect

    def test_production_adapter_rejects_redirect_to_resolved_prohibited(self):
        adapter = SafeHTTPAdapter()
        original_getaddrinfo = socket.getaddrinfo
        original_make_request = urllib3.connectionpool.HTTPConnectionPool._make_request
        original_connect = socket.socket.connect
        connected_addresses = []

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "initial.example.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))]
            if host == "prohibited.example.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", port))]
            return original_getaddrinfo(host, port, *args, **kwargs)

        def fake_make_request(pool, conn, method, url, *args, **kwargs):
            if pool.host == "initial.example.com":
                # Return a redirect for the first (public) request. Following the
                # redirect should be blocked before any socket connects to the
                # prohibited resolved address.
                return urllib3.HTTPResponse(
                    body=BytesIO(b""),
                    headers={"Location": "http://prohibited.example.com/"},
                    status=302,
                    version=11,
                    reason="Found",
                    preload_content=True,
                )
            return original_make_request(pool, conn, method, url, *args, **kwargs)

        def fake_connect(self, address):
            connected_addresses.append(address)
            raise OSError("Intentional abort")

        socket.getaddrinfo = fake_getaddrinfo
        urllib3.connectionpool.HTTPConnectionPool._make_request = fake_make_request
        socket.socket.connect = fake_connect
        try:
            import requests
            session = requests.Session()
            session.mount("http://", adapter)
            with self.assertRaises((SSRFError, requests.exceptions.ConnectionError)):
                session.get("http://initial.example.com/", timeout=1)
            self.assertNotIn(("127.0.0.1", 80), connected_addresses)
        finally:
            socket.getaddrinfo = original_getaddrinfo
            urllib3.connectionpool.HTTPConnectionPool._make_request = original_make_request
            socket.socket.connect = original_connect

    def test_https_rejects_untrusted_certificate(self):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        crt = os.path.join(fixtures_dir, "test_localhost.crt")
        key = os.path.join(fixtures_dir, "test_localhost.key")
        with local_http_server(https=True, cert_file=crt, key_file=key) as port:
            import requests
            session = requests.Session()
            session.mount("https://", TestSafeHTTPAdapter())
            with self.assertRaises(requests.exceptions.SSLError):
                session.get(f"https://localhost:{port}/", timeout=5)

    def test_https_rejects_hostname_mismatch(self):
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        crt = os.path.join(fixtures_dir, "test_localhost.crt")
        key = os.path.join(fixtures_dir, "test_localhost.key")
        original_getaddrinfo = socket.getaddrinfo

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "wronghost.example.com":
                return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", port))]
            return original_getaddrinfo(host, port, *args, **kwargs)

        socket.getaddrinfo = fake_getaddrinfo
        try:
            with local_http_server(https=True, cert_file=crt, key_file=key) as port:
                import requests
                session = requests.Session()
                session.mount("https://", TestSafeHTTPAdapter())
                with self.assertRaises(requests.exceptions.SSLError):
                    session.get(
                        f"https://wronghost.example.com:{port}/",
                        timeout=5,
                        verify=crt,
                    )
        finally:
            socket.getaddrinfo = original_getaddrinfo


# ---------- Secret scanner tests ----------

class SecretScannerTests(TestCase):
    def test_normalizes_camelcase_key(self):
        self.assertEqual(normalize_key("apiKey"), "apikey")

    def test_normalizes_underscore_key(self):
        self.assertEqual(normalize_key("API_KEY"), "apikey")

    def test_normalizes_hyphen_key(self):
        self.assertEqual(normalize_key("api-key"), "apikey")

    def test_detects_secret_key_at_top_level(self):
        self.assertEqual(scan_for_secrets({"apiKey": "abc"}), ["apiKey"])

    def test_detects_secret_key_nested(self):
        self.assertEqual(
            scan_for_secrets({"nested": {"auth_token": "abc"}}),
            ["auth_token"],
        )

    def test_detects_secret_key_in_list(self):
        self.assertEqual(
            scan_for_secrets({"items": [{"password": "secret"}]}),
            ["password"],
        )

    def test_allows_safe_keys(self):
        self.assertEqual(scan_for_secrets({"board_token": "stripe"}), [])


# ---------- Normalizer tests ----------

class NormalizerTests(TestCase):
    def test_normalize_text_collapse_spaces(self):
        self.assertEqual(normalize_text("  hello   world  "), "hello world")

    def test_normalize_external_id_strips_whitespace(self):
        self.assertEqual(normalize_external_id("  abc  "), "abc")

    def test_normalize_external_id_blank_becomes_none(self):
        self.assertIsNone(normalize_external_id("   "))

    def test_normalize_url_rejects_ssrf_target(self):
        self.assertEqual(normalize_url("http://127.0.0.1/"), "")

    def test_normalize_url_makes_relative_absolute(self):
        self.assertEqual(
            normalize_url("/jobs/1", "https://example.com"),
            "https://example.com/jobs/1",
        )

    def test_normalize_date_parses_iso(self):
        self.assertEqual(normalize_date("2026-10-28").isoformat(), "2026-10-28")

    def test_normalize_salary_range(self):
        self.assertEqual(normalize_salary("$50,000 - $70,000"), (50000, 70000))

    def test_normalize_salary_single_value(self):
        self.assertEqual(normalize_salary("50000"), (50000, 50000))

    def test_normalize_employment_type_maps_common_values(self):
        self.assertEqual(normalize_employment_type("Full-time"), "full_time")

    def test_normalize_seniority_maps_entry(self):
        self.assertEqual(normalize_seniority("Entry-level"), "entry")

    def test_strip_html(self):
        self.assertEqual(strip_html("<p>Hello</p>"), "Hello")


# ---------- Adapter tests ----------

class CsvAdapterTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(name="LinkedIn Export")

    def test_csv_import_creates_jobs(self):
        csv_data = """title,company,location,external_id
Software Engineer,Acme,Remote,abc1
Data Scientist,Corp,NY,abc2"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_data)
            path = f.name
        try:
            adapter = CsvAdapter(self.source, {"file": path})
            records = list(adapter.fetch())
            self.assertEqual(len(records), 2)
            normalized = adapter.normalize(records[0])
            self.assertEqual(normalized["title"], "Software Engineer")
            self.assertEqual(normalized["_source_name"], "LinkedIn Export")
        finally:
            os.unlink(path)


class JsonAdapterTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(name="JSON Export")

    def test_json_import_creates_jobs(self):
        data = [
            {"title": "Nurse", "company": "Hospital", "external_id": "n1"},
            {"title": "Teacher", "company": "School", "external_id": "t1"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            adapter = JsonAdapter(self.source, {"file": path})
            records = list(adapter.fetch())
            self.assertEqual(len(records), 2)
            normalized = adapter.normalize(records[0])
            self.assertEqual(normalized["title"], "Nurse")
        finally:
            os.unlink(path)


class UsaJobsAdapterTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(name="USAJobs")

    @patch("core.adapters.impl.usajobs.fetch_url")
    def test_usajobs_adapter_normalizes_records(self, mock_fetch_url):
        os.environ["USAJOBS_API_KEY"] = "test-key"

        first_page = {
            "SearchResult": {
                "SearchResultItems": [
                    {
                        "MatchedObjectDescriptor": {
                            "PositionID": "12345",
                            "PositionTitle": "Nurse",
                            "PositionURI": "https://example.com/job/12345",
                            "PositionLocation": [{"LocationName": "New York, NY"}],
                            "PublicationStartDate": "2026-10-01",
                            "ApplicationCloseDate": "2026-10-31",
                            "PositionRemuneration": [
                                {
                                    "MinimumRange": "50000",
                                    "MaximumRange": "70000",
                                    "Description": "Per Year",
                                }
                            ],
                            "PositionSchedule": [{"Name": "Full-time"}],
                            "UserArea": {
                                "Details": {
                                    "JobSummary": "Provide care.",
                                    "CareerLevel": "Senior",
                                }
                            },
                        }
                    }
                ]
            }
        }
        second_page = {"SearchResult": {"SearchResultItems": []}}

        mock_fetch_url.side_effect = [
            FetchedResponse(
                url="https://data.usajobs.gov/api/search",
                status_code=200,
                headers={},
                content=b"",
                text=json.dumps(first_page),
            ),
            FetchedResponse(
                url="https://data.usajobs.gov/api/search",
                status_code=200,
                headers={},
                content=b"",
                text=json.dumps(second_page),
            ),
        ]
        adapter = UsaJobsAdapter(self.source, {"keywords": "nurse"})
        records = list(adapter.fetch())
        self.assertEqual(len(records), 1)
        normalized = adapter.normalize(records[0])
        self.assertEqual(normalized["title"], "Nurse")
        self.assertEqual(normalized["external_id"], "12345")
        self.assertTrue(adapter.fetch_complete)


class GreenhouseAdapterTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(
            name="Stripe Careers",
            config={"board_token": "stripe"},
        )

    @patch("core.adapters.impl.greenhouse.fetch_url")
    def test_greenhouse_adapter_normalizes_records(self, mock_fetch_url):
        mock_fetch_url.return_value = FetchedResponse(
            url="https://boards-api.greenhouse.io/v1/boards/stripe/jobs",
            status_code=200,
            headers={},
            content=b"",
            text=json.dumps({
                "jobs": [
                    {
                        "id": 1,
                        "title": "Software Engineer",
                        "absolute_url": "https://boards.greenhouse.io/stripe/jobs/1",
                        "location": {"name": "Remote"},
                        "content": "<p>Build things.</p>",
                        "updated_at": "2026-10-28T00:00:00Z",
                    }
                ]
            }),
        )
        adapter = GreenhouseAdapter(self.source, {})
        records = list(adapter.fetch())
        self.assertEqual(len(records), 1)
        normalized = adapter.normalize(records[0])
        self.assertEqual(normalized["title"], "Software Engineer")
        self.assertEqual(normalized["external_id"], "1")
        self.assertTrue(adapter.fetch_complete)


class LeverAdapterTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(
            name="Lever Example",
            config={"board_token": "lever"},
        )

    @patch("core.adapters.impl.lever.fetch_url")
    def test_lever_adapter_normalizes_records(self, mock_fetch_url):
        mock_fetch_url.return_value = FetchedResponse(
            url="https://api.lever.co/v0/postings/lever?mode=json",
            status_code=200,
            headers={},
            content=b"",
            text=json.dumps([
                {
                    "id": "a1b2c3",
                    "text": "Software Engineer",
                    "hostedUrl": "https://jobs.lever.co/lever/a1b2c3",
                    "description": "<p>Build things.</p>",
                    "createdAt": 1710000000000,
                    "categories": {
                        "location": "Remote",
                        "commitment": "Full-time",
                        "team": "Engineering",
                        "level": "Senior",
                    },
                }
            ]),
        )
        adapter = LeverAdapter(self.source, {})
        records = list(adapter.fetch())
        self.assertEqual(len(records), 1)
        normalized = adapter.normalize(records[0])
        self.assertEqual(normalized["title"], "Software Engineer")
        self.assertEqual(normalized["external_id"], "a1b2c3")
        self.assertTrue(adapter.fetch_complete)


class AshbyAdapterTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(
            name="Ashby Example",
            config={"board_name": "ashby"},
        )

    @patch("core.adapters.impl.ashby.fetch_url")
    def test_ashby_adapter_normalizes_records(self, mock_fetch_url):
        mock_fetch_url.return_value = FetchedResponse(
            url="https://api.ashbyhq.com/posting-api/job-board/ashby",
            status_code=200,
            headers={},
            content=b"",
            text=json.dumps({
                "jobs": [
                    {
                        "id": "job-123",
                        "title": "Senior Product Designer",
                        "department": "Design",
                        "team": "Product Design",
                        "employmentType": "FullTime",
                        "workplaceType": "Hybrid",
                        "location": "New York, NY",
                        "publishedAt": "2026-10-01T00:00:00.000Z",
                        "jobUrl": "https://jobs.ashbyhq.com/ashby/job-123",
                        "applyUrl": "https://jobs.ashbyhq.com/ashby/job-123/apply",
                        "descriptionHtml": "<p>Design great products.</p>",
                    }
                ]
            }),
        )
        adapter = AshbyAdapter(self.source, {})
        records = list(adapter.fetch())
        self.assertEqual(len(records), 1)
        normalized = adapter.normalize(records[0])
        self.assertEqual(normalized["title"], "Senior Product Designer")
        self.assertEqual(normalized["external_id"], "job-123")
        self.assertEqual(normalized["employment_type"], "full_time")
        self.assertEqual(normalized["work_arrangement"], "hybrid")
        self.assertTrue(adapter.fetch_complete)


class RssAdapterTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(name="Example Jobs Feed")

    @patch("core.adapters.impl.rss.fetch_url")
    def test_rss_adapter_normalizes_records(self, mock_fetch_url):
        mock_fetch_url.return_value = FetchedResponse(
            url="https://example.com/feed.xml",
            status_code=200,
            headers={},
            content=b"""<?xml version="1.0"?>
<rss version="2.0">
<channel>
<item>
<title>Developer</title>
<link>https://example.com/jobs/dev</link>
<description>Build software.</description>
<pubDate>Mon, 28 Oct 2026 00:00:00 GMT</pubDate>
</item>
</channel>
</rss>""",
            text="",
        )
        adapter = RssAdapter(self.source, {"feed_url": "https://example.com/feed.xml"})
        records = list(adapter.fetch())
        self.assertEqual(len(records), 1)
        normalized = adapter.normalize(records[0])
        self.assertEqual(normalized["title"], "Developer")
        self.assertTrue(adapter.fetch_complete)


class RemoteOkAdapterTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(name="Remote OK")

    @patch("core.adapters.impl.remoteok.can_fetch", return_value=True)
    @patch("core.adapters.impl.remoteok.fetch_url")
    def test_remoteok_adapter_skips_metadata_and_normalizes(self, mock_fetch, mock_can):
        mock_fetch.return_value = FetchedResponse(
            url="https://remoteok.com/api",
            status_code=200,
            headers={},
            content=b"",
            text=json.dumps([
                {"legal": "terms", "payment": "info"},
                {
                    "id": 123,
                    "position": "Data Analyst",
                    "company": "Remote Co",
                    "description": "<p>Use Python and SQL.</p>",
                    "location": "Worldwide",
                    "url": "https://remoteok.com/remote-jobs/123",
                    "date": "2026-11-01",
                    "tags": ["data", "python", "sql"],
                    "salary_min": 60000,
                    "salary_max": 90000,
                },
            ]),
        )
        adapter = RemoteOkAdapter(self.source, {})
        records = list(adapter.fetch())
        self.assertEqual(len(records), 1)

        normalized = adapter.normalize(records[0])
        self.assertEqual(normalized["title"], "Data Analyst")
        self.assertEqual(normalized["company"], "Remote Co")
        self.assertEqual(normalized["work_arrangement"], "remote")
        self.assertEqual(normalized["source_url"], "https://remoteok.com/remote-jobs/123")
        self.assertIn("Python", normalized["description"])
        self.assertTrue(adapter.fetch_complete)

    @patch("core.adapters.impl.remoteok.can_fetch", return_value=False)
    def test_remoteok_adapter_respects_robots_txt(self, mock_can):
        adapter = RemoteOkAdapter(self.source, {})
        with self.assertRaises(ValueError):
            list(adapter.fetch())


# ---------- IngestionService tests ----------

class FakeAdapter(BaseAdapter):
    def __init__(self, source, records, fetch_complete=True):
        super().__init__(source, {})
        self._records = records
        self.fetch_complete = fetch_complete

    def fetch(self):
        return iter(self._records)

    def normalize(self, raw):
        return {
            "title": raw.get("title"),
            "company": raw.get("company"),
            "description": raw.get("description", ""),
            "location": raw.get("location", ""),
            "industry": raw.get("industry", ""),
            "source_url": raw.get("source_url", ""),
            "external_id": raw.get("external_id"),
            "employment_type": raw.get("employment_type", ""),
            "work_arrangement": raw.get("work_arrangement", ""),
            "seniority": raw.get("seniority", ""),
            "salary_min": raw.get("salary_min"),
            "salary_max": raw.get("salary_max"),
            "salary_currency": raw.get("salary_currency", "USD"),
            "salary_period": raw.get("salary_period", ""),
            "date_posted": raw.get("date_posted"),
            "application_deadline": raw.get("application_deadline"),
            "_source_name": raw.get("_source_name", self.source.name),
            "raw_data": raw,
        }


class IngestionServiceTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(name="Test Source")

    def test_create_new_jobs(self):
        adapter = FakeAdapter(self.source, [
            {"title": "Engineer", "company": "Co", "external_id": "e1"},
            {"title": "Designer", "company": "Co", "external_id": "e2"},
        ])
        summary = IngestionService(self.source, adapter).run()
        self.assertEqual(summary.total_records, 2)
        self.assertEqual(summary.created, 2)
        self.assertTrue(summary.validate_counts())
        self.assertEqual(Job.objects.count(), 2)

    def test_update_existing_job(self):
        Job.objects.create(
            title="Old Title",
            company="Co",
            source=self.source,
            external_id="e1",
            application_status=Job.ApplicationStatus.SAVED,
        )
        adapter = FakeAdapter(self.source, [
            {"title": "New Title", "company": "Co", "external_id": "e1"},
        ])
        summary = IngestionService(self.source, adapter).run()
        self.assertEqual(summary.updated, 1)
        job = Job.objects.get(external_id="e1")
        self.assertEqual(job.title, "New Title")

    def test_preserves_application_status_on_update(self):
        Job.objects.create(
            title="Title",
            company="Co",
            source=self.source,
            external_id="e1",
            application_status=Job.ApplicationStatus.APPLIED,
        )
        adapter = FakeAdapter(self.source, [
            {"title": "Title", "company": "Co", "external_id": "e1"},
        ])
        IngestionService(self.source, adapter).run()
        job = Job.objects.get(external_id="e1")
        self.assertEqual(job.application_status, Job.ApplicationStatus.APPLIED)

    def test_update_drops_inconsistent_merged_salary(self):
        Job.objects.create(
            title="Title",
            company="Co",
            source=self.source,
            external_id="e1",
            salary_min=100,
            salary_max=200,
            salary_currency="USD",
            salary_period="yearly",
        )
        # Incoming record only provides a max that is lower than existing min.
        adapter = FakeAdapter(self.source, [
            {"title": "Title", "company": "Co", "external_id": "e1", "salary_max": 50},
        ])
        summary = IngestionService(self.source, adapter).run()
        self.assertEqual(summary.updated, 1)
        self.assertEqual(summary.salary_dropped, 1)
        job = Job.objects.get(external_id="e1")
        self.assertIsNone(job.salary_min)
        self.assertIsNone(job.salary_max)
        self.assertEqual(job.salary_currency, "")
        self.assertEqual(job.salary_period, "")

    def test_invalid_salary_drops_salary_and_keeps_job(self):
        adapter = FakeAdapter(self.source, [
            {"title": "Job", "company": "Co", "external_id": "e1", "salary_min": 200, "salary_max": 100},
        ])
        summary = IngestionService(self.source, adapter).run()
        self.assertEqual(summary.failed, 0)
        self.assertEqual(summary.created, 1)
        self.assertEqual(summary.salary_dropped, 1)
        job = Job.objects.get(external_id="e1")
        self.assertIsNone(job.salary_min)
        self.assertIsNone(job.salary_max)

    def test_source_name_mismatch_fails_record(self):
        adapter = FakeAdapter(self.source, [
            {"title": "Job", "company": "Co", "external_id": "e1", "_source_name": "Wrong Source"},
        ])
        summary = IngestionService(self.source, adapter).run()
        self.assertEqual(summary.failed, 1)

    def test_duplicate_hash_computed(self):
        adapter = FakeAdapter(self.source, [
            {"title": "Engineer", "company": "Co", "external_id": "e1", "location": "Remote"},
        ])
        IngestionService(self.source, adapter).run()
        job = Job.objects.get(external_id="e1")
        expected = hashlib.sha256("engineer|co|remote".encode()).hexdigest()
        self.assertEqual(job.duplicate_hash, expected)

    def test_count_reconciliation(self):
        adapter = FakeAdapter(self.source, [
            {"title": "A", "company": "Co", "external_id": "e1"},
            {"title": "B", "company": "Co", "external_id": "e2"},
            {"title": "C", "company": "Co", "external_id": "e3", "salary_min": 200, "salary_max": 100},
        ])
        summary = IngestionService(self.source, adapter).run()
        self.assertEqual(summary.total_records, 3)
        self.assertEqual(summary.created, 3)
        self.assertEqual(summary.salary_dropped, 1)
        self.assertEqual(summary.failed, 0)
        self.assertTrue(summary.validate_counts())


# ---------- Expired-listing tests ----------

class ExpiryTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(name="Test Source")

    def test_expiry_runs_when_complete_and_successful(self):
        Job.objects.create(
            title="Old",
            company="Co",
            source=self.source,
            external_id="gone",
            source_listing_status=Job.SourceListingStatus.ACTIVE,
        )
        adapter = FakeAdapter(self.source, [
            {"title": "New", "company": "Co", "external_id": "present"},
        ])
        summary = IngestionService(self.source, adapter).run(mark_expired=True)
        self.assertEqual(summary.failed, 0)
        old = Job.objects.get(external_id="gone")
        self.assertEqual(old.source_listing_status, Job.SourceListingStatus.EXPIRED)

    def test_expiry_skipped_when_record_fails(self):
        Job.objects.create(
            title="Old",
            company="Co",
            source=self.source,
            external_id="gone",
            source_listing_status=Job.SourceListingStatus.ACTIVE,
        )
        adapter = FakeAdapter(self.source, [
            {"title": "New", "company": "Co", "external_id": "present"},
            {"title": "Bad", "company": "Co", "external_id": "bad", "_source_name": "Wrong Source"},
        ])
        summary = IngestionService(self.source, adapter).run(mark_expired=True)
        self.assertEqual(summary.failed, 1)
        old = Job.objects.get(external_id="gone")
        self.assertEqual(old.source_listing_status, Job.SourceListingStatus.ACTIVE)

    def test_expiry_skipped_for_zero_record_fetch(self):
        Job.objects.create(
            title="Old",
            company="Co",
            source=self.source,
            external_id="gone",
            source_listing_status=Job.SourceListingStatus.ACTIVE,
        )
        adapter = FakeAdapter(self.source, [], fetch_complete=True)
        summary = IngestionService(self.source, adapter).run(mark_expired=True)
        self.assertEqual(summary.total_records, 0)
        old = Job.objects.get(external_id="gone")
        self.assertEqual(old.source_listing_status, Job.SourceListingStatus.ACTIVE)

    def test_expiry_preserves_application_status(self):
        job = Job.objects.create(
            title="Old",
            company="Co",
            source=self.source,
            external_id="gone",
            source_listing_status=Job.SourceListingStatus.ACTIVE,
            application_status=Job.ApplicationStatus.APPLIED,
        )
        adapter = FakeAdapter(self.source, [
            {"title": "New", "company": "Co", "external_id": "present"},
        ])
        IngestionService(self.source, adapter).run(mark_expired=True)
        job.refresh_from_db()
        self.assertEqual(job.application_status, Job.ApplicationStatus.APPLIED)


# ---------- Management command tests ----------

class IngestCommandTests(TestCase):
    def setUp(self):
        self.source = JobSource.objects.create(name="CSV Source")

    def test_command_imports_csv(self):
        csv_data = """title,company,external_id
Engineer,Co,e1"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_data)
            path = f.name
        try:
            call_command("ingest", adapter="csv", source_name="CSV Source", file=path)
            self.assertTrue(Job.objects.filter(external_id="e1").exists())
        finally:
            os.unlink(path)

    def test_command_fails_for_unknown_adapter(self):
        with self.assertRaises(CommandError):
            call_command("ingest", adapter="unknown", source_name="CSV Source")

    def test_command_fails_for_missing_source(self):
        with self.assertRaises(CommandError):
            call_command("ingest", adapter="csv", source_name="Missing Source")

    def test_command_creates_source_when_requested(self):
        csv_data = "title,company,external_id\nEngineer,Co,e1"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_data)
            path = f.name
        try:
            call_command(
                "ingest",
                adapter="csv",
                source_name="New Source",
                create_source=True,
                file=path,
            )
            self.assertTrue(JobSource.objects.filter(name="New Source").exists())
        finally:
            os.unlink(path)


# ---------- Source registry/config tests ----------

class SourceRegistryTests(TestCase):
    def test_adapter_registry_contains_expected_adapters(self):
        for key in ["csv", "json", "usajobs", "greenhouse", "rss", "lever", "ashby"]:
            self.assertIsNotNone(get_adapter_class(key))

    def test_config_rejects_secret_key(self):
        source = JobSource.objects.create(name="Secret Source")
        from core.serializers import JobSourceSerializer
        serializer = JobSourceSerializer(source, data={"config": {"apiKey": "abc"}}, partial=True)
        self.assertFalse(serializer.is_valid())

    def test_config_allows_public_token(self):
        source = JobSource.objects.create(name="Public Source")
        from core.serializers import JobSourceSerializer
        serializer = JobSourceSerializer(source, data={"config": {"board_token": "stripe"}}, partial=True)
        self.assertTrue(serializer.is_valid())


# ---------- Regression tests ----------

class RegressionTests(AuthenticatedAPITestCase):
    def test_health_endpoint_still_works(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"status": "ok"})

    def test_job_filter_includes_source_listing_status(self):
        source = JobSource.objects.create(name="Src")
        Job.objects.create(
            title="Job",
            company="Co",
            source=source,
            source_listing_status=Job.SourceListingStatus.EXPIRED,
        )
        response = self.client.get("/api/jobs/?source_listing_status=expired")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
