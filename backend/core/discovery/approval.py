"""Approve verified employer candidates and create shared JobSource rows.

Approval is the bridge between a user-private candidate and a shared source.
Sources are deduplicated by adapter and board token so the same board cannot be
created twice.
"""
from core.models import EmployerCandidate, JobSource


class ApprovalError(Exception):
    """Raised when a candidate cannot be approved."""


def _source_name(adapter_key: str, board_value: str) -> str:
    """Deterministic shared source name from adapter + sanitized token."""
    return f"{adapter_key}:{board_value}"


def _config_key(adapter_key: str) -> str:
    return "board_name" if adapter_key == "ashby" else "board_token"


def approve_candidate(candidate: EmployerCandidate) -> JobSource:
    """Create or get a shared JobSource for a verified candidate."""
    if candidate.status != EmployerCandidate.Status.FOUND:
        raise ApprovalError("Candidate must be verified as found before approval")
    if not candidate.adapter_key or not candidate.board_value:
        raise ApprovalError("Candidate is missing adapter or board value")

    name = _source_name(candidate.adapter_key, candidate.board_value)
    source, created = JobSource.objects.get_or_create(
        name=name,
        defaults={
            "source_type": JobSource.SourceType.ATS,
            "access_method": JobSource.AccessMethod.API,
            "is_active": False,
            "base_url": "",
            "config": {
                "adapter_key": candidate.adapter_key,
                _config_key(candidate.adapter_key): candidate.board_value,
            },
        },
    )
    candidate.status = EmployerCandidate.Status.APPROVED
    candidate.save(update_fields=["status", "updated_at"])
    return source


def reject_candidate(candidate: EmployerCandidate):
    """Mark a candidate as rejected."""
    candidate.status = EmployerCandidate.Status.REJECTED
    candidate.save(update_fields=["status", "updated_at"])
