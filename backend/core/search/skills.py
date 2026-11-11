"""Skill matching with aliases and whole-word checks."""
import re

# Map canonical skill names to aliases that may appear in text.
SKILL_ALIASES: dict[str, list[str]] = {
    "R": ["r"],
    "C": ["c"],
    "Go": ["go", "golang"],
    "Python": ["python"],
    "JavaScript": ["javascript", "js"],
    "TypeScript": ["typescript", "ts"],
    "SQL": ["sql"],
    "Excel": ["excel"],
    "Tableau": ["tableau"],
    "Power BI": ["power bi", "powerbi"],
    "Django": ["django"],
    "React": ["react"],
    "AWS": ["aws", "amazon web services"],
    "Azure": ["azure"],
    "GCP": ["gcp", "google cloud"],
}


def _canonical_skills() -> list[str]:
    return list(SKILL_ALIASES.keys())


def _token_pattern(alias: str) -> re.Pattern:
    """Return a pattern that matches *alias* as a whole word/phrase."""
    escaped = re.escape(alias)
    if len(alias) == 1:
        # Single-letter skills must not be adjacent to other word characters.
        return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)
    return re.compile(rf"\b{escaped}\b", re.IGNORECASE)


def find_skills_in_text(text: str) -> set[str]:
    """Return the set of canonical skills found in *text*."""
    found = set()
    if not text:
        return found
    for skill, aliases in SKILL_ALIASES.items():
        for alias in aliases:
            if _token_pattern(alias).search(text):
                found.add(skill)
                break
    return found


def skill_overlap(profile_skills: list[str], job_text: str) -> set[str]:
    """Return the subset of profile skills present in *job_text*."""
    if not profile_skills or not job_text:
        return set()
    job_skills = find_skills_in_text(job_text)
    normalized_profile = {s.lower().strip() for s in profile_skills}
    matched = set()
    for skill in job_skills:
        if skill.lower() in normalized_profile:
            matched.add(skill)
        # Also check aliases the user may have typed.
        for alias in SKILL_ALIASES.get(skill, []):
            if alias.lower() in normalized_profile:
                matched.add(skill)
                break
    return matched
