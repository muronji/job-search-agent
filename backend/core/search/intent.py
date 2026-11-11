"""Search intent dataclass and fake parser."""
from dataclasses import dataclass, field


@dataclass
class SearchIntent:
    """Structured, industry-agnostic job-search intent."""

    keywords: str = ""
    exclude_keywords: list[str] = field(default_factory=list)
    job_titles: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    seniority_levels: list[str] = field(default_factory=list)
    employment_types: list[str] = field(default_factory=list)
    work_arrangements: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = "USD"
    posted_within_days: int | None = None


class FakeProvider:
    """Test-only intent provider. Uses simple keyword heuristics, no network."""

    # Map free-text tokens to model choice keys.
    _SENIORITY = {
        "entry": "entry",
        "junior": "junior",
        "mid": "mid",
        "senior": "senior",
        "lead": "lead",
        "manager": "manager",
        "executive": "executive",
    }
    _EMPLOYMENT = {
        "full-time": "full_time",
        "full time": "full_time",
        "part-time": "part_time",
        "part time": "part_time",
        "contract": "contract",
        "internship": "internship",
        "temporary": "temporary",
        "freelance": "freelance",
        "volunteer": "volunteer",
    }
    _WORK_ARRANGEMENT = {
        "remote": "remote",
        "hybrid": "hybrid",
        "onsite": "onsite",
        "on-site": "onsite",
    }

    def parse_intent(self, text: str) -> SearchIntent:
        if not text:
            return SearchIntent()

        lower = text.lower()
        intent = SearchIntent(keywords=text.strip())

        for token, key in self._SENIORITY.items():
            if token in lower and key not in intent.seniority_levels:
                intent.seniority_levels.append(key)

        for token, key in self._EMPLOYMENT.items():
            if token in lower and key not in intent.employment_types:
                intent.employment_types.append(key)

        for token, key in self._WORK_ARRANGEMENT.items():
            if token in lower and key not in intent.work_arrangements:
                intent.work_arrangements.append(key)

        return intent
