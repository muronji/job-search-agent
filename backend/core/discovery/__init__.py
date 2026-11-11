"""Bounded discovery of employer ATS boards.

All probes use fetch_url, so they inherit SSRF protection, robots.txt checks,
and per-domain rate limiting.
"""
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from core.models import JobSource
from core.utils.http import (
    BudgetExhaustedError,
    FetchError,
    RateLimitError,
    _status_from_fetch_error,
    can_fetch,
    fetch_url,
)


@dataclass
class ProbeResult:
    adapter_key: str
    board_value: str
    display_name: str
    job_count: int = 0
    raw_data: Any = None


@dataclass
class ProbeOutcome:
    adapter_key: str
    board_value: str
    status: str  # 'found', 'not_found', 'forbidden', 'error'
    detail: str = ""
    http_status: int | None = None
    result: ProbeResult | None = None


@dataclass
class FetchResult:
    """Result of attempting to fetch JSON from a probe URL."""

    ok: bool
    data: Any = None
    error_status: str = ""  # 'not_found', 'forbidden', 'error'
    detail: str = ""
    http_status: int | None = None


def _normalized_board_name(name: str) -> str:
    """Turn a company name into a plausible board token."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _fetch_json(url: str) -> FetchResult:
    """Fetch JSON from *url*, returning a structured result.

    Only an explicit HTTP 404 is reported as ``not_found``. Everything else
    that prevents us from obtaining a valid JSON jobs payload is reported as
    ``forbidden`` or ``error`` with a reason.
    """
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"

    if not can_fetch(url):
        return FetchResult(
            ok=False,
            error_status="forbidden",
            detail=f"robots.txt disallows fetches from {host}",
        )

    response = None
    try:
        response = fetch_url(url, timeout=10, wait=True)
        data = response.json()
    except BudgetExhaustedError as exc:
        return FetchResult(
            ok=False,
            error_status="error",
            detail=f"budget exhausted: {exc}",
        )
    except RateLimitError as exc:
        return FetchResult(
            ok=False,
            error_status="error",
            detail=f"rate limited: {exc}",
        )
    except FetchError as exc:
        status = _status_from_fetch_error(exc)
        if status == 404:
            return FetchResult(
                ok=False,
                error_status="not_found",
                detail=f"{host}{path} returned 404",
                http_status=status,
            )
        if status == 403:
            return FetchResult(
                ok=False,
                error_status="forbidden",
                detail=f"{host}{path} returned 403",
                http_status=status,
            )
        return FetchResult(
            ok=False,
            error_status="error",
            detail=f"{host}{path} fetch failed: {exc}",
            http_status=status,
        )
    except json.JSONDecodeError as exc:
        first_bytes = (
            response.content[:4].hex() if getattr(response, "content", None) else "empty"
        )
        encoding = getattr(response, "content_encoding", None) or "none"
        return FetchResult(
            ok=False,
            error_status="error",
            detail=(
                f"{host}{path} returned invalid JSON "
                f"(Content-Encoding: {encoding}, first bytes: {first_bytes}): {exc}"
            ),
            http_status=getattr(response, "status_code", None),
        )
    except ValueError as exc:
        return FetchResult(
            ok=False,
            error_status="error",
            detail=f"{host}{path} response error: {exc}",
            http_status=getattr(response, "status_code", None),
        )

    return FetchResult(ok=True, data=data, http_status=response.status_code)


def _make_outcome(adapter_key: str, board_value: str, result: FetchResult) -> ProbeOutcome:
    if not result.ok:
        return ProbeOutcome(
            adapter_key=adapter_key,
            board_value=board_value,
            status=result.error_status,
            detail=result.detail,
            http_status=result.http_status,
        )

    jobs = None
    data = result.data
    if adapter_key == "greenhouse" and isinstance(data, dict):
        jobs = data.get("jobs")
    elif adapter_key == "lever" and isinstance(data, list):
        jobs = data
    elif adapter_key == "ashby" and isinstance(data, dict):
        jobs = data.get("jobs")

    if isinstance(jobs, list):
        probe_result = ProbeResult(
            adapter_key, board_value, board_value, len(jobs), raw_data=data
        )
        return ProbeOutcome(
            adapter_key=adapter_key,
            board_value=board_value,
            status="found",
            http_status=result.http_status,
            result=probe_result,
        )

    return ProbeOutcome(
        adapter_key=adapter_key,
        board_value=board_value,
        status="error",
        detail=f"unexpected response shape from {adapter_key}",
        http_status=result.http_status,
    )


def probe_greenhouse(board_token: str) -> ProbeOutcome:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    result = _fetch_json(url)
    return _make_outcome("greenhouse", board_token, result)


def probe_lever(site: str) -> ProbeOutcome:
    url = f"https://api.lever.co/v0/postings/{site}?mode=json"
    result = _fetch_json(url)
    return _make_outcome("lever", site, result)


def probe_ashby(board_name: str) -> ProbeOutcome:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board_name}"
    result = _fetch_json(url)
    return _make_outcome("ashby", board_name, result)


PROBES = {
    "greenhouse": probe_greenhouse,
    "lever": probe_lever,
    "ashby": probe_ashby,
}


def probe_employer(entry: dict) -> list[ProbeOutcome]:
    """Return ProbeOutcomes for a single seed entry.

    entry example:
        {"name": "Stripe", "greenhouse": "stripe", "lever": "stripe"}
    """
    outcomes = []
    for key, probe in PROBES.items():
        value = entry.get(key)
        if not value:
            continue
        outcomes.append(probe(value))
    return outcomes


def probe_employer_name(company_name: str) -> list[ProbeOutcome]:
    """Probe the three public ATS patterns for *company_name*.

    Returns verified results as inactive candidates. The number of probes is
    bounded by the number of ATS patterns (3).
    """
    token = _normalized_board_name(company_name)
    if not token:
        return []
    outcomes = []
    for adapter_key, probe in PROBES.items():
        outcomes.append(probe(token))
    return outcomes


def seed_entry_for_result(company_name: str, result: ProbeResult) -> dict:
    """Return a JSON-ready seed entry for a verified board."""
    config_key = "board_name" if result.adapter_key == "ashby" else "board_token"
    return {
        "name": company_name,
        result.adapter_key: result.board_value,
        "_comment": f"{result.job_count} job(s) found",
    }


def create_candidate_source(company_name: str, result: ProbeResult):
    """Create or get an inactive JobSource for a verified board."""
    config_key = "board_name" if result.adapter_key == "ashby" else "board_token"
    source, created = JobSource.objects.get_or_create(
        name=company_name,
        defaults={
            "source_type": JobSource.SourceType.ATS,
            "access_method": JobSource.AccessMethod.API,
            "is_active": False,
            "base_url": "",
            "config": {
                "adapter_key": result.adapter_key,
                config_key: result.board_value,
            },
        },
    )
    return source, created
