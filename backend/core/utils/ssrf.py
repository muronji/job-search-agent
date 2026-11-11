"""SSRF-safe URL validation and HTTP adapter built on requests/urllib3."""
import ipaddress
import socket
from urllib.parse import urlparse

import urllib3.connection
import urllib3.poolmanager
from requests.adapters import HTTPAdapter


class SSRFError(Exception):
    """Raised when a URL or connection is rejected for SSRF safety."""


def _is_public_ip(addr: ipaddress._BaseAddress) -> bool:
    """Return True only for globally routable, non-reserved, non-multicast IPs.

    ``ip_address.is_global`` rejects loopback, private, link-local, reserved,
    and documentation ranges on Python 3.13, including ``100.64.0.0/10``,
    ``192.0.0.0/24``, ``198.18.0.0/15``, ``2001:db8::/32``, and IPv4-mapped
    IPv6 private addresses such as ``::ffff:10.0.0.1``. Multicast and the
    unspecified address are excluded explicitly for defense in depth.
    """
    return addr.is_global and not addr.is_multicast and not addr.is_unspecified


def _normalize_ip_host(host: str):
    """Return an ipaddress object for unusual IP representations, or None."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if host.isdigit():
        try:
            return ipaddress.ip_address(int(host))
        except ValueError:
            pass
    if host.startswith("0x") or host.startswith("0X"):
        try:
            return ipaddress.ip_address(int(host, 16))
        except ValueError:
            pass
    parts = host.split(".")
    if len(parts) == 4 and all(parts):
        values = []
        ok = True
        for part in parts:
            try:
                if part.startswith("0x") or part.startswith("0X"):
                    values.append(int(part, 16))
                elif len(part) > 1 and part.startswith("0"):
                    values.append(int(part, 8))
                else:
                    values.append(int(part, 10))
            except ValueError:
                ok = False
                break
        if ok and all(0 <= v <= 255 for v in values):
            try:
                return ipaddress.ip_address(".".join(str(v) for v in values))
            except ValueError:
                pass
    return None


def validate_url(url: str) -> None:
    """Validate that *url* is safe to fetch in production.

    Raises SSRFError for non-HTTP(S) schemes, embedded credentials, literal
    loopback/private hostnames, and unusual IP representations.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SSRFError(f"Scheme not allowed: {parsed.scheme}")
    if parsed.username is not None or parsed.password is not None:
        raise SSRFError("Embedded credentials are not allowed")
    host = parsed.hostname
    if not host:
        raise SSRFError("Missing host")
    if host.lower() in {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"}:
        raise SSRFError(f"Host not allowed: {host}")
    norm = _normalize_ip_host(host)
    if norm is not None and not _is_public_ip(norm):
        raise SSRFError(f"IP address not allowed: {host}")


def _validate_resolved_addresses(host: str, port: int, allow_loopback: bool) -> str:
    """Resolve *host* and return the first safe IP to connect to.

    Raises SSRFError if no safe address is found.
    """
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise urllib3.exceptions.NameResolutionError(host, None, e) from e

    validated = []
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if allow_loopback:
            if addr.is_loopback:
                validated.append((ip_str, addr))
                continue
        if _is_public_ip(addr):
            validated.append((ip_str, addr))

    if not validated:
        raise SSRFError(f"No public IP found for host: {host}")

    if allow_loopback:
        validated.sort(key=lambda item: 0 if item[1].version == 4 else 1)

    return validated[0][0]


class SafeHTTPConnection(urllib3.connection.HTTPConnection):
    """HTTPConnection that pins DNS resolution to validated public IPs."""

    # Production default. Test-only subclasses may override this.
    _ssrf_allow_loopback = False

    def _new_conn(self):
        original_host = self._dns_host
        allow_loopback = self._ssrf_allow_loopback
        if not allow_loopback:
            norm = _normalize_ip_host(original_host)
            if norm is not None and not _is_public_ip(norm):
                raise SSRFError(f"Host not allowed: {original_host}")

        chosen_ip = _validate_resolved_addresses(
            original_host, self.port, allow_loopback
        )
        self._dns_host = chosen_ip
        try:
            sock = super()._new_conn()
        finally:
            self._dns_host = original_host
        return sock


class SafeHTTPSConnection(urllib3.connection.HTTPSConnection, SafeHTTPConnection):
    """HTTPSConnection that inherits safe connection pinning."""


class SafeHTTPConnectionPool(urllib3.HTTPConnectionPool):
    ConnectionCls = SafeHTTPConnection


class SafeHTTPSConnectionPool(urllib3.HTTPSConnectionPool):
    ConnectionCls = SafeHTTPSConnection


class SafePoolManager(urllib3.PoolManager):
    def _new_pool(self, scheme, host, port, request_context=None):
        if scheme == "http":
            return SafeHTTPConnectionPool(host, port, **self.connection_pool_kw)
        if scheme == "https":
            return SafeHTTPSConnectionPool(host, port, **self.connection_pool_kw)
        raise SSRFError(f"Scheme not allowed: {scheme}")


class SafeHTTPAdapter(HTTPAdapter):
    """Production requests adapter with SSRF-safe connection pinning.

    Every URL (including redirects) is validated before any connection is made.
    Loopback and private addresses are rejected. Certificate verification is
    never disabled.
    """

    _ssrf_allow_loopback = False

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self.poolmanager = SafePoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )

    def _validate_request_url(self, url: str) -> None:
        validate_url(url)

    def send(self, request, **kwargs):
        self._validate_request_url(request.url)
        return super().send(request, **kwargs)


def get_safe_session(timeout: int = 30) -> "requests.Session":
    """Return a requests Session mounted with SafeHTTPAdapter."""
    import requests

    session = requests.Session()
    adapter = SafeHTTPAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.timeout = timeout
    return session
