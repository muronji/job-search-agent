"""SSRF-safe HTTP fetching with politeness controls."""
import gzip
import io
import json
import time
import zlib
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from requests.exceptions import RequestException

from core.utils.ssrf import SafeHTTPAdapter, validate_url


DEFAULT_TIMEOUT = 30
DEFAULT_MAX_SIZE = 5 * 1024 * 1024
USER_AGENT = "job-search-agent/0.1 (+https://example.com/contact)"
DEFAULT_MIN_INTERVAL = 1.0


class FetchError(Exception):
    """Raised when a safe fetch fails for any reason."""


class RateLimitError(Exception):
    """Raised when a per-domain rate limit is hit."""

    def __init__(self, message: str, wait_seconds: float | None = None):
        super().__init__(message)
        self.wait_seconds = wait_seconds


class BudgetExhaustedError(RateLimitError):
    """Raised when a scan/request budget is exhausted; do not auto-wait."""


@dataclass(frozen=True)
class FetchedResponse:
    """A simplified, safe response object."""

    url: str
    status_code: int
    headers: dict
    content: bytes
    text: str
    content_encoding: str = ""

    def json(self):
        return json.loads(self.text)


def _decompress_gzip(data: bytes, max_size: int, url: str) -> bytes:
    """Decompress *data* with gzip, raising if decoded size exceeds *max_size*."""
    output = bytearray()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > max_size:
                    raise ValueError(
                        f"Response from {url} exceeds {max_size} bytes"
                    )
    except (gzip.BadGzipFile, OSError, EOFError) as e:
        raise ValueError(
            f"Failed to decompress gzip response from {url}: {e}"
        ) from e
    return bytes(output)


def _decompress_deflate(data: bytes, max_size: int, url: str) -> bytes:
    """Decompress *data* with deflate (raw or zlib-wrapped), capping decoded size."""
    # Try raw deflate first, then zlib-wrapped, matching urllib3's behavior.
    for wbits in (-zlib.MAX_WBITS, zlib.MAX_WBITS):
        output = bytearray()
        try:
            decompressor = zlib.decompressobj(wbits)
            for i in range(0, len(data), 65536):
                chunk = decompressor.decompress(data[i : i + 65536])
                output.extend(chunk)
                if len(output) > max_size:
                    raise ValueError(
                        f"Response from {url} exceeds {max_size} bytes"
                    )
            chunk = decompressor.flush()
            output.extend(chunk)
            if len(output) > max_size:
                raise ValueError(
                    f"Response from {url} exceeds {max_size} bytes"
                )
            return bytes(output)
        except zlib.error:
            continue
    raise ValueError(f"Failed to decompress deflate response from {url}")


class DomainRateLimiter:
    """In-memory per-domain request throttle."""

    def __init__(self, min_interval: float = DEFAULT_MIN_INTERVAL):
        self._min_interval = min_interval
        self._last: dict[str, float] = {}

    def check(self, domain: str):
        now = time.monotonic()
        last = self._last.get(domain)
        if last is not None and now - last < self._min_interval:
            wait = self._min_interval - (now - last)
            raise RateLimitError(
                f"Rate limit for {domain}: wait {wait:.1f}s",
                wait_seconds=wait,
            )
        self._last[domain] = now

    def reset(self):
        self._last.clear()


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()


# Module-level limiter. Tests may replace this instance or pass one explicitly.
_default_rate_limiter = DomainRateLimiter()


def fetch_url(
    url: str,
    *,
    method: str = "GET",
    timeout: float = DEFAULT_TIMEOUT,
    max_size: int = DEFAULT_MAX_SIZE,
    rate_limiter: DomainRateLimiter | None = None,
    user_agent: str = USER_AGENT,
    wait: bool = False,
    max_wait: float = 5.0,
    **request_kwargs,
) -> FetchedResponse:
    """Fetch *url* through the SSRF-safe adapter.

    Raises SSRFError for unsafe URLs, RateLimitError for throttled domains,
    and FetchError for network or size errors.

    If *wait* is True and the domain rate limit requires a short delay, the
    function sleeps up to *max_wait* seconds and retries once. Budget-exhaustion
    errors are always re-raised.
    """
    validate_url(url)
    limiter = rate_limiter if rate_limiter is not None else _default_rate_limiter
    try:
        limiter.check(_domain(url))
    except BudgetExhaustedError:
        raise
    except RateLimitError as exc:
        wait_seconds = exc.wait_seconds
        if not wait or wait_seconds is None or wait_seconds > max_wait:
            raise
        time.sleep(wait_seconds)
        limiter.check(_domain(url))

    session = requests.Session()
    session.mount("http://", SafeHTTPAdapter())
    session.mount("https://", SafeHTTPAdapter())
    session.headers["User-Agent"] = user_agent

    try:
        with session.request(
            method, url, timeout=timeout, stream=True, **request_kwargs
        ) as resp:
            resp.raise_for_status()
            content_encoding = resp.headers.get("Content-Encoding", "").lower()
            if content_encoding and content_encoding not in (
                "gzip",
                "x-gzip",
                "deflate",
            ):
                raise FetchError(
                    f"Unsupported Content-Encoding: {content_encoding}"
                )

            # Cap the number of compressed bytes read from the wire.
            compressed = resp.raw.read(max_size + 1, decode_content=False)
            if len(compressed) > max_size:
                raise ValueError(
                    f"Compressed response from {url} exceeds {max_size} bytes"
                )

            # Decompress if necessary; the cap below applies to decoded bytes.
            if content_encoding in ("gzip", "x-gzip"):
                content = _decompress_gzip(compressed, max_size, url)
            elif content_encoding == "deflate":
                content = _decompress_deflate(compressed, max_size, url)
            else:
                content = compressed

            if len(content) > max_size:
                raise ValueError(
                    f"Response from {url} exceeds {max_size} bytes"
                )

            return FetchedResponse(
                url=resp.url,
                status_code=resp.status_code,
                headers=dict(resp.headers),
                content=content,
                text=content.decode("utf-8", errors="replace"),
                content_encoding=content_encoding,
            )
    except ValueError as e:
        raise FetchError(f"Fetch failed for {url}: {e}") from e
    except RequestException as e:
        raise FetchError(f"Fetch failed for {url}: {e}") from e
    finally:
        session.close()


def _status_from_fetch_error(exc: FetchError) -> int | None:
    """Return the HTTP status code wrapped in a FetchError, if any."""
    cause = exc.__cause__
    if cause is None:
        return None
    response = getattr(cause, "response", None)
    if response is not None:
        return getattr(response, "status_code", None)
    return None


def can_fetch(url: str, user_agent: str = USER_AGENT) -> bool:
    """Return True if *url* is allowed by the site's robots.txt.

    Per RFC 9309, a missing robots.txt (4xx) means no restrictions, while a
    server error (5xx) or a network failure means the site must be treated as
    disallowing all fetches.
    """
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = fetch_url(robots_url, max_size=1024 * 1024)
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(user_agent, url)
    except FetchError as exc:
        status = _status_from_fetch_error(exc)
        if status is not None and 400 <= status < 500:
            return True
        return False
    except Exception:
        return False
