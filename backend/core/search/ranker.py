"""Rule-based ranking of jobs against a user's profile.

This is intentionally a heuristic. Scores are 0-100, with matched/unmatched/unknown
criteria returned for transparency.
"""
import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.models import Job, UserPreference
from core.search.countries import detect_countries
from core.search.intent import SearchIntent
from core.search.skills import skill_overlap


STOP_WORDS = {"the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "of", "with"}


def _infer_target_titles(profile: dict) -> list[str]:
    titles = []
    for exp in profile.get("experience", []) or []:
        title = exp.get("title")
        if title:
            titles.append(title)
    prefs = profile.get("preferences", {}) or {}
    titles.extend(prefs.get("job_titles", []))
    return list(dict.fromkeys(titles))


def _infer_locations(profile: dict) -> list[str]:
    prefs = profile.get("preferences", {}) or {}
    locations = list(prefs.get("locations", []))
    own_location = profile.get("location")
    if own_location:
        locations.append(own_location)
    return list(dict.fromkeys(locations))


def _infer_seniority_levels(profile: dict) -> list[str]:
    prefs = profile.get("preferences", {}) or {}
    levels = list(prefs.get("seniority_levels", []))
    # Infer from experience titles as a simple hint.
    title_text = " ".join(_infer_target_titles(profile)).lower()
    if "senior" in title_text or "lead" in title_text or "manager" in title_text:
        for key in ["senior", "lead", "manager"]:
            if key not in levels:
                levels.append(key)
    elif "junior" in title_text or "entry" in title_text:
        for key in ["junior", "entry"]:
            if key not in levels:
                levels.append(key)
    return list(dict.fromkeys(levels))


def build_intent_from_profile(user) -> SearchIntent:
    """Build a deterministic SearchIntent from the user's profile and prefs."""
    profile = getattr(user, "cv_profile", None)
    parsed = profile.parsed_profile if profile else {}
    if not isinstance(parsed, dict):
        parsed = {}

    prefs, _ = UserPreference.objects.get_or_create(user=user)

    work_arrangements = parsed.get("preferences", {}).get("work_arrangements", [])

    return SearchIntent(
        job_titles=_infer_target_titles(parsed),
        industries=parsed.get("preferences", {}).get("industries", []),
        locations=_infer_locations(parsed),
        seniority_levels=_infer_seniority_levels(parsed),
        employment_types=parsed.get("preferences", {}).get("employment_types", []),
        work_arrangements=work_arrangements,
        skills=parsed.get("skills", []),
        salary_min=parsed.get("preferences", {}).get("min_salary") or prefs.min_salary,
        salary_currency=prefs.salary_currency or "USD",
    )


def _job_text(job: Job) -> str:
    parts = [job.title, job.description, job.industry, job.location]
    return " ".join(p for p in parts if p).lower()


def _job_text_cached(job: Job, text: str | None) -> str:
    return text if text is not None else _job_text(job)


def _title_matches(target_titles: list[str], job_title: str) -> bool:
    if not target_titles or not job_title:
        return False
    lower_job = job_title.lower()
    job_tokens = {t for t in re.findall(r"\b[a-z0-9+]+\b", lower_job) if t not in STOP_WORDS}
    for title in target_titles:
        lower_title = title.lower()
        if lower_title in lower_job:
            return True
        title_tokens = {t for t in re.findall(r"\b[a-z0-9+]+\b", lower_title) if t not in STOP_WORDS}
        if title_tokens and title_tokens & job_tokens:
            return True
    return False


def _location_matches(intent: SearchIntent, job: Job, job_countries: set[str] | None = None) -> bool:
    if not intent.locations:
        return False
    countries = job_countries if job_countries is not None else set(detect_countries(job.location))
    job_location_lower = (job.location or "").lower()
    for loc in intent.locations:
        if loc.lower() in job_location_lower:
            return True
        if countries & set(detect_countries(loc)):
            return True
    return False


def _remote_matches(intent: SearchIntent, job: Job) -> bool:
    wants_remote = "remote" in intent.work_arrangements or "hybrid" in intent.work_arrangements
    if not wants_remote:
        return False
    return job.work_arrangement in (Job.WorkArrangement.REMOTE, Job.WorkArrangement.HYBRID)


def _seniority_matches(intent: SearchIntent, job: Job) -> bool:
    if not intent.seniority_levels or not job.seniority:
        return False
    return job.seniority in intent.seniority_levels


def _salary_matches(intent: SearchIntent, job: Job) -> bool:
    if intent.salary_min is None:
        return False
    if job.salary_max is not None:
        return job.salary_max >= intent.salary_min
    if job.salary_min is not None:
        return job.salary_min >= intent.salary_min
    return False


def _is_fresh(job: Job) -> bool:
    if job.date_posted:
        cutoff = timezone.now().date() - timedelta(days=settings.RANK_FRESHNESS_DAYS)
        return job.date_posted >= cutoff
    if job.date_discovered:
        cutoff = timezone.now() - timedelta(days=settings.RANK_FRESHNESS_DAYS)
        return job.date_discovered >= cutoff
    return False


def rank_job(
    job: Job,
    intent: SearchIntent,
    *,
    job_text: str | None = None,
    job_countries: set[str] | None = None,
) -> dict:
    """Return score, verdict, and criterion lists for *job* against *intent*."""
    weights = settings.RANK_WEIGHTS
    matched = []
    unmatched = []
    unknown = []
    earned = 0
    possible = 0

    text = _job_text_cached(job, job_text)
    profile_skills = intent.skills

    # Title
    possible += weights["title"]
    if intent.job_titles:
        if _title_matches(intent.job_titles, job.title):
            earned += weights["title"]
            matched.append("title")
        else:
            unmatched.append("title")
    else:
        unknown.append("title")

    # Skills
    possible += weights["skills"]
    if profile_skills:
        overlap = skill_overlap(profile_skills, text)
        if overlap:
            earned += weights["skills"]
            matched.append(f"skills ({', '.join(sorted(overlap))})")
        else:
            unmatched.append("skills")
    else:
        unknown.append("skills")

    # Location / country
    possible += weights["location"]
    if intent.locations:
        if _location_matches(intent, job, job_countries):
            earned += weights["location"]
            matched.append("location")
        else:
            unmatched.append("location")
    else:
        unknown.append("location")

    # Remote fit
    possible += weights["remote"]
    if intent.work_arrangements and ("remote" in intent.work_arrangements or "hybrid" in intent.work_arrangements):
        if _remote_matches(intent, job):
            earned += weights["remote"]
            matched.append("remote")
        else:
            unmatched.append("remote")
    else:
        unknown.append("remote")

    # Seniority
    possible += weights["seniority"]
    if intent.seniority_levels:
        if _seniority_matches(intent, job):
            earned += weights["seniority"]
            matched.append("seniority")
        else:
            unmatched.append("seniority")
    else:
        unknown.append("seniority")

    # Salary
    possible += weights["salary"]
    if intent.salary_min is not None:
        if job.salary_min is not None or job.salary_max is not None:
            if _salary_matches(intent, job):
                earned += weights["salary"]
                matched.append("salary")
            else:
                unmatched.append("salary")
        else:
            unknown.append("salary")
    else:
        unknown.append("salary")

    # Freshness
    possible += weights["freshness"]
    if job.date_posted or job.date_discovered:
        if _is_fresh(job):
            earned += weights["freshness"]
            matched.append("freshness")
        else:
            unmatched.append("freshness")
    else:
        unknown.append("freshness")

    score = round(earned / possible * 100) if possible > 0 else 0
    apply_threshold = settings.RANK_APPLY_THRESHOLD
    track_threshold = settings.RANK_TRACK_THRESHOLD
    if score >= apply_threshold:
        verdict = "apply"
    elif score >= track_threshold:
        verdict = "track"
    else:
        verdict = "ignore"

    return {
        "score": score,
        "verdict": verdict,
        "matched_criteria": matched,
        "unmatched_criteria": unmatched,
        "unknown_criteria": unknown,
        "heuristic": True,
    }


