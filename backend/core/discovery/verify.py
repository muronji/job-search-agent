"""Bounded verification of employer-candidate boards.

Verification turns a suggested company name into candidate ATS boards using the
existing Greenhouse, Lever and Ashby probes. It is bounded (two name variants,
three adapters) so it cannot be used to scan arbitrary names indefinitely.
"""
import re
from datetime import timedelta
from urllib.parse import unquote

from django.utils import timezone

from core import discovery
from core.models import EmployerCandidate


class VerificationError(Exception):
    """Raised when verification cannot proceed or is unsafe."""


MAX_BOARD_TOKEN_LENGTH = 50
MAX_VARIANTS = 2
MAX_SAMPLE_JOBS = 3
NO_REPROBE_DELTA = timedelta(days=30)

# Access probes through the module so tests can patch core.discovery.probe_*.
PROBES = {
    "greenhouse": lambda v: discovery.probe_greenhouse(v),
    "lever": lambda v: discovery.probe_lever(v),
    "ashby": lambda v: discovery.probe_ashby(v),
}


def sanitize_board_token(value: str) -> str | None:
    """Return a safe board token containing only [a-z0-9-], or None."""
    if not value:
        return None
    # Decode percent-encoded characters so %2F does not become part of a token.
    decoded = unquote(value)
    lower = decoded.lower()
    # Replace spaces and underscores with hyphens, drop everything else.
    token = re.sub(r"[^a-z0-9-]+", "-", lower)
    # Collapse multiple hyphens.
    token = re.sub(r"-+", "-", token).strip("-")
    token = token[:MAX_BOARD_TOKEN_LENGTH]
    return token if token else None


def _build_variants(display_name: str, normalized_name: str) -> list[str]:
    """Return up to two sanitized board-name variants."""
    variants = []
    first = sanitize_board_token(normalized_name)
    if first:
        variants.append(first)
    second = sanitize_board_token(display_name)
    if second and second not in variants:
        variants.append(second)
    return variants[:MAX_VARIANTS]


def _extract_sample_jobs(adapter_key: str, data) -> list[dict]:
    """Return a few sanitized sample jobs from a successful probe response."""
    jobs = []
    if data is None:
        return []
    if adapter_key == "greenhouse" and isinstance(data, dict):
        jobs = data.get("jobs", [])
    elif adapter_key == "lever" and isinstance(data, list):
        jobs = data
    elif adapter_key == "ashby" and isinstance(data, dict):
        jobs = data.get("jobs", [])

    samples = []
    for job in jobs[:MAX_SAMPLE_JOBS]:
        if not isinstance(job, dict):
            continue
        title = ""
        location = ""
        if adapter_key == "greenhouse":
            title = job.get("title", "")
            loc = job.get("location")
            if isinstance(loc, dict):
                location = loc.get("name", "")
            elif isinstance(loc, str):
                location = loc
        elif adapter_key == "lever":
            title = job.get("text", "")
            categories = job.get("categories") or {}
            location = categories.get("location", "")
        elif adapter_key == "ashby":
            title = job.get("title", "")
            location = job.get("location", "")
        sample = {
            "title": str(title)[:200],
            "location": str(location)[:200],
        }
        if sample["title"] or sample["location"]:
            samples.append(sample)
    return samples


def verify_candidate(candidate: EmployerCandidate) -> EmployerCandidate:
    """Probe adapters for *candidate* and update its status.

    A previously successful ``no_supported_board`` result is not reprobed for
    30 days. Blocked or error outcomes leave the candidate as ``suggested`` so
    a later run can retry.
    """
    if candidate.status == EmployerCandidate.Status.APPROVED:
        raise VerificationError("Candidate is already approved")
    if candidate.status == EmployerCandidate.Status.REJECTED:
        raise VerificationError("Candidate is already rejected")
    if candidate.status == EmployerCandidate.Status.NO_SUPPORTED_BOARD:
        if (
            candidate.last_probed_at
            and (timezone.now() - candidate.last_probed_at) < NO_REPROBE_DELTA
        ):
            raise VerificationError(
                "No supported board was found recently; reprobe allowed after 30 days"
            )

    variants = _build_variants(candidate.display_name, candidate.normalized_name)
    if not variants:
        raise VerificationError("Candidate name cannot be turned into a board token")

    outcomes = []
    for variant in variants:
        for adapter_key, probe in PROBES.items():
            outcome = probe(variant)
            outcomes.append(outcome)
            if outcome.status == "found" and outcome.result:
                sample_jobs = _extract_sample_jobs(adapter_key, outcome.result.raw_data)
                candidate.status = EmployerCandidate.Status.FOUND
                candidate.adapter_key = adapter_key
                candidate.board_value = variant
                candidate.sample_jobs = sample_jobs
                candidate.reason = (
                    f"Found {outcome.result.job_count} job(s) on {adapter_key} "
                    f"board '{variant}'."
                )[:500]
                candidate.last_probed_at = timezone.now()
                candidate.probe_count += 1
                candidate.save()
                return candidate

    # No board found. Classify the aggregate outcome.
    statuses = {o.status for o in outcomes}
    if "forbidden" in statuses or "error" in statuses:
        # Keep as suggested so a later run can retry; record a short reason.
        detail = "; ".join(
            f"{o.adapter_key}: {o.status}" for o in outcomes
            if o.status in {"forbidden", "error"}
        )[:500]
        candidate.reason = detail
        candidate.last_probed_at = timezone.now()
        candidate.probe_count += 1
        candidate.save()
        return candidate

    # All adapters reported not_found.
    candidate.status = EmployerCandidate.Status.NO_SUPPORTED_BOARD
    candidate.reason = "No supported ATS board found for this name."
    candidate.last_probed_at = timezone.now()
    candidate.probe_count += 1
    candidate.save()
    return candidate
