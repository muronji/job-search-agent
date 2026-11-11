"""Batch-1 scan-agent scoring.

Score = Fit x Access x Level, each 1-5, total out of 125.
Everything is computed on request; nothing is stored per job.
"""

import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.models import Job, JobSource, UserPreference
from core.search.countries import COUNTRY_ALIASES, detect_countries, detect_country


# Text patterns used for access and level heuristics.
_SPONSORSHIP_PHRASES = [
    "visa sponsorship",
    "sponsorship available",
    "we sponsor",
    "relocation assistance",
    "willing to relocate",
]

_CONTRACTOR_PHRASES = [
    "contractor",
    "contract role",
    "employer of record",
    "eor",
]

_BLOCKED_PHRASES = [
    "existing right to work",
    "must have the right to work",
    "right to work in",
    "citizenship required",
    "clearance required",
    "security clearance",
    "secret clearance",
    "ts/sci",
]

_SENIOR_WORDS = ["senior", "lead", "principal", "staff", "manager", "director", "head of"]
_ENTRY_WORDS = ["graduate", "trainee", "intern", "entry-level", "entry level", "junior"]


def _parse_year(value: str) -> int | None:
    if not value:
        return None
    match = re.search(r"\b(19|20)\d{2}\b", str(value))
    return int(match.group(0)) if match else None


def compute_user_years(experience: list[dict]) -> int | None:
    """Approximate total years of experience from date strings."""
    if not experience:
        return None
    current_year = timezone.now().year
    total = 0
    counted = False
    for exp in experience:
        start = _parse_year(exp.get("start_date"))
        end_text = exp.get("end_date", "")
        end = _parse_year(end_text)
        if end is None:
            if isinstance(end_text, str) and "present" in end_text.lower():
                end = current_year
            else:
                end = current_year
        if start and end and end >= start:
            total += end - start
            counted = True
    return total if counted else None


def _job_text(job: Job) -> str:
    parts = [job.title, job.company, job.description, job.location]
    return " ".join(p for p in parts if p)


def generate_default_scan_profile(parsed_profile: dict) -> dict:
    """Build a sensible starting scan profile from a parsed CV."""
    parsed_profile = parsed_profile or {}
    home_country = detect_country(parsed_profile.get("location", ""))
    years = compute_user_years(parsed_profile.get("experience", []) or [])
    prefs = parsed_profile.get("preferences", {}) or {}
    work_arrangements = prefs.get("work_arrangements", []) or []
    remote_enabled = not work_arrangements or any(
        w in work_arrangements for w in ("remote", "hybrid")
    )

    access_table = []
    if home_country:
        access_table.append({
            "country": home_country,
            "score": 5,
            "reason": "Home country",
            "last_reviewed": None,
        })
    if remote_enabled:
        access_table.append({
            "country": "Remote",
            "score": 5,
            "reason": "Remote role open to home country",
            "last_reviewed": None,
        })
        access_table.append({
            "country": "Remote contractor/EOR",
            "score": 4,
            "reason": "Remote contractor or employer-of-record",
            "last_reviewed": None,
        })
    access_table.append({
        "country": "Other",
        "score": 1,
        "reason": "Other country - work rights not established",
        "last_reviewed": None,
    })

    return {
        "home_country": home_country,
        "relocation_countries": [],
        "remote_enabled": remote_enabled,
        "years_of_experience": years,
        "access_table": access_table,
        "gates": {
            "exclude_countries": [],
            "exclude_role_types": [],
            "exclude_unpaid": True,
            "max_years_required": years,
            "clearance_phrases": [
                "security clearance",
                "secret clearance",
                "ts/sci",
                "clearance required",
            ],
            "missing_certifications": [],
        },
        "thresholds": {
            "apply": getattr(settings, "SCAN_APPLY_THRESHOLD", 75),
            "track": getattr(settings, "SCAN_TRACK_THRESHOLD", 50),
            "ignore": getattr(settings, "SCAN_IGNORE_THRESHOLD", 30),
        },
        "role_families": [],
    }


def get_or_build_scan_profile(user) -> dict:
    """Return the user's scan profile, generating defaults if empty."""
    preference, _ = UserPreference.objects.get_or_create(user=user)
    if not preference.scan_profile:
        profile = getattr(user, "cv_profile", None)
        parsed = profile.parsed_profile if profile else {}
        preference.scan_profile = generate_default_scan_profile(parsed)
        preference.save(update_fields=["scan_profile"])
    return preference.scan_profile


def is_confirmed_source(job: Job) -> bool:
    """True for listings from an employer's own job-board API."""
    source = job.source
    if not source:
        return False
    return (
        source.source_type == JobSource.SourceType.ATS
        and source.access_method == JobSource.AccessMethod.API
    )


def _extract_required_years(job: Job) -> int | None:
    text = f"{job.title or ''} {job.description or ''}".lower()
    matches = re.finditer(r"(\d+)\s*(?:[-\u2013]\s*\d+)?\+?\s*years?\b", text)
    years = [int(m.group(1)) for m in matches]
    return min(years) if years else None


def _blocked_reason(job: Job, job_text: str | None = None) -> str | None:
    text = (job_text or _job_text(job)).lower()
    for phrase in getattr(settings, "SCAN_BLOCKED_PHRASES", _BLOCKED_PHRASES):
        if phrase.lower() in text:
            return f"blocked: {phrase}"
    return None


def compute_access_score(
    job: Job,
    scan_profile: dict,
    *,
    job_text: str | None = None,
    job_countries: set[str] | None = None,
) -> tuple[int | None, str]:
    """Return a 1-5 access score and a plain reason."""
    text = job_text or _job_text(job)
    blocked = _blocked_reason(job, text)
    if blocked:
        return 1, blocked

    location = (job.location or "").lower()
    text = text.lower()
    countries = job_countries if job_countries is not None else set(detect_countries(job.location))
    is_remote = (
        job.work_arrangement == Job.WorkArrangement.REMOTE
        or "remote" in location
    )
    remote_enabled = scan_profile.get("remote_enabled", True)
    home_country = scan_profile.get("home_country")
    relocation = {c for c in scan_profile.get("relocation_countries", []) or []}

    matching_scores = []
    access_table = scan_profile.get("access_table", [])

    for row in access_table:
        country = row.get("country", "")
        score = row.get("score")
        reason = row.get("reason", "")
        if score is None:
            continue

        if country == home_country and country in countries:
            matching_scores.append((score, reason or "in home country"))
            continue

        if country in COUNTRY_ALIASES and country in countries:
            if country in relocation:
                sponsorship = any(p in text for p in _SPONSORSHIP_PHRASES)
                if sponsorship and score < 3:
                    matching_scores.append((3, "relocation country, mentions sponsorship"))
                else:
                    matching_scores.append((score, reason or f"in {country}"))
            else:
                matching_scores.append((score, reason or f"in {country}"))
            continue

        if country == "Remote" and is_remote and remote_enabled:
            # Only score as open remote if the location is unrestricted
            # or explicitly mentions the home country / relocation / worldwide.
            restricted = countries and not any(
                c in (home_country, "Remote") or c in relocation
                for c in countries
            )
            if not restricted:
                matching_scores.append((score, reason or "remote role"))
            continue

        if country == "Remote contractor/EOR" and is_remote and remote_enabled:
            if any(p in text for p in _CONTRACTOR_PHRASES):
                matching_scores.append((score, reason or "remote contractor/EOR"))
            continue

        if country == "Other" and countries:
            matching_scores.append((score, reason or "other country"))
            continue

    if matching_scores:
        best_score, best_reason = max(matching_scores, key=lambda item: item[0])
        return best_score, best_reason

    return None, "location not mapped"


def compute_level_score(
    job: Job,
    scan_profile: dict,
    required_years: int | None = None,
) -> tuple[int | None, str]:
    """Return a 1-5 level score relative to the user's experience."""
    title_lower = (job.title or "").lower()
    user_years = scan_profile.get("years_of_experience")

    if any(word in title_lower for word in _ENTRY_WORDS):
        return 5, "entry-level role"

    if required_years is None:
        required_years = _extract_required_years(job)
    if required_years is None:
        return None, "years required not stated"
    if user_years is None:
        return None, "your experience is unknown"

    diff = user_years - required_years
    if diff >= 4:
        score = 5
    elif diff >= 1:
        score = 4
    elif diff >= -1:
        score = 3
    elif diff >= -3:
        score = 2
    else:
        score = 1

    reason = f"requires ~{required_years} years; you have ~{user_years}"
    senior = any(word in title_lower for word in _SENIOR_WORDS)
    if senior and diff < 0:
        score = max(1, score - 1)
        reason += " (senior title)"
    return score, reason


def map_fit_score(rule_based_score: int) -> int:
    """Map the 0-100 rule-based score to a 1-5 Fit score."""
    if rule_based_score >= 80:
        return 5
    if rule_based_score >= 60:
        return 4
    if rule_based_score >= 40:
        return 3
    if rule_based_score >= 20:
        return 2
    return 1


def apply_gates(
    job: Job,
    scan_profile: dict,
    *,
    required_years: int | None = None,
    job_countries: set[str] | None = None,
    job_text: str | None = None,
) -> dict:
    """Return gate reasons and a verdict cap, if any."""
    if required_years is None:
        required_years = _extract_required_years(job)
    user_years = scan_profile.get("years_of_experience")
    gates = scan_profile.get("gates", {}) or {}
    text = (job_text or _job_text(job)).lower()

    ignore_reasons = []
    track_reasons = []

    exclude_countries = {c.lower() for c in gates.get("exclude_countries", []) or []}
    if exclude_countries:
        countries = job_countries if job_countries is not None else set(detect_countries(job.location))
        if {c.lower() for c in countries} & exclude_countries:
            ignore_reasons.append("excluded country")

    exclude_role_types = {r.lower() for r in gates.get("exclude_role_types", []) or []}
    if job.employment_type and job.employment_type.lower() in exclude_role_types:
        ignore_reasons.append("excluded role type")
    for role_type in exclude_role_types:
        if role_type in (job.title or "").lower():
            ignore_reasons.append("excluded role type")
            break

    if gates.get("exclude_unpaid", True):
        if job.employment_type == Job.EmploymentType.VOLUNTEER:
            ignore_reasons.append("unpaid role")
        elif job.salary_min == 0 and job.salary_max == 0:
            ignore_reasons.append("unpaid role")
        elif "unpaid" in text:
            ignore_reasons.append("unpaid role")

    max_years = gates.get("max_years_required")
    if required_years is not None and max_years is not None and required_years > max_years:
        track_reasons.append(f"requires {required_years}+ years; your max is {max_years}")

    clearance_phrases = gates.get("clearance_phrases", [])
    if clearance_phrases and any(p.lower() in text for p in clearance_phrases):
        track_reasons.append("may require security clearance")

    for cert in gates.get("missing_certifications", []) or []:
        if cert.lower() in text:
            track_reasons.append(f"may require {cert} certification")
            break

    if ignore_reasons:
        cap = "ignore"
    elif track_reasons:
        cap = "track"
    else:
        cap = None

    return {
        "ignore_reasons": ignore_reasons,
        "track_reasons": track_reasons,
        "verdict_cap": cap,
    }


def compute_verdict(total_score: int, gate_result: dict, is_confirmed: bool) -> tuple[str, bool]:
    """Return verdict string and whether the role is an unconfirmed lead."""
    thresholds = gate_result.get("thresholds", {
        "apply": getattr(settings, "SCAN_APPLY_THRESHOLD", 75),
        "track": getattr(settings, "SCAN_TRACK_THRESHOLD", 50),
        "ignore": getattr(settings, "SCAN_IGNORE_THRESHOLD", 30),
    })

    cap = gate_result.get("verdict_cap")

    if total_score < thresholds["ignore"]:
        base = "ignore"
    elif total_score < thresholds["apply"]:
        base = "track"
    else:
        base = "apply"

    if cap == "ignore":
        verdict = "ignore"
    elif cap == "track" and base == "apply":
        verdict = "track"
    else:
        verdict = base

    is_lead = False
    if verdict in ("apply", "track") and not is_confirmed:
        is_lead = True
        if verdict == "apply":
            verdict = "track"

    return verdict, is_lead


def score_job(
    job: Job,
    fit_rank: dict,
    scan_profile: dict,
    *,
    job_text: str | None = None,
    job_countries: set[str] | None = None,
    required_years: int | None = None,
) -> dict:
    """Return the full Batch-1 score payload for a job."""
    text = job_text or _job_text(job)
    countries = job_countries if job_countries is not None else set(detect_countries(job.location))
    years_required = required_years if required_years is not None else _extract_required_years(job)

    fit_score = map_fit_score(fit_rank.get("score", 0) or 0)
    access_score, access_reason = compute_access_score(
        job, scan_profile, job_text=text, job_countries=countries
    )
    level_score, level_reason = compute_level_score(job, scan_profile, required_years=years_required)

    effective_access = access_score if access_score is not None else 3
    effective_level = level_score if level_score is not None else 3
    total_score = fit_score * effective_access * effective_level

    is_confirmed = is_confirmed_source(job)
    gate_result = apply_gates(
        job,
        scan_profile,
        required_years=years_required,
        job_countries=countries,
        job_text=text,
    )
    # Thresholds live in the scan profile; ensure gate_result can read them.
    gate_result["thresholds"] = scan_profile.get("thresholds", {
        "apply": getattr(settings, "SCAN_APPLY_THRESHOLD", 75),
        "track": getattr(settings, "SCAN_TRACK_THRESHOLD", 50),
        "ignore": getattr(settings, "SCAN_IGNORE_THRESHOLD", 30),
    })
    verdict, is_lead = compute_verdict(total_score, gate_result, is_confirmed)

    deadline = job.application_deadline
    closing_soon = False
    if deadline:
        closing_soon = deadline <= (timezone.now().date() + timedelta(days=7))

    return {
        "fit_score": fit_score,
        "access_score": access_score,
        "level_score": level_score,
        "total_score": total_score,
        "verdict": verdict,
        "is_confirmed": is_confirmed,
        "is_lead": is_lead,
        "matched_criteria": fit_rank.get("matched_criteria", []),
        "unmatched_criteria": fit_rank.get("unmatched_criteria", []),
        "unknown_criteria": fit_rank.get("unknown_criteria", []),
        "access_reason": access_reason,
        "level_reason": level_reason,
        "gate_reasons": gate_result["ignore_reasons"] + gate_result["track_reasons"],
        "closing_soon": closing_soon,
        "heuristic": True,
    }
