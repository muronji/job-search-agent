"""Adapter for the Ashby public job-board API.

The public endpoint used here is ``https://api.ashbyhq.com/posting-api/job-board/{name}``.
It is unauthenticated and separate from Ashby's authenticated API. The field
names and response shape were observed from independent public requests and
community documentation, not from Ashby's own official API documentation.
"""
from typing import Iterable

from core.adapters.base import BaseAdapter
from core.adapters.normalizers import (
    normalize_date,
    normalize_external_id,
    normalize_seniority,
    normalize_text,
    normalize_url,
    normalize_work_arrangement,
    strip_html,
)
from core.utils.http import fetch_url


def _map_employment_type(value):
    if not value:
        return ""
    text = str(value).lower()
    if "full" in text:
        return "full_time"
    if "part" in text:
        return "part_time"
    if "contract" in text:
        return "contract"
    if "intern" in text:
        return "internship"
    if "temp" in text:
        return "temporary"
    if "free" in text:
        return "freelance"
    if "volunteer" in text:
        return "volunteer"
    return ""


class AshbyAdapter(BaseAdapter):
    """Fetch public job postings from an Ashby job board.

    Config:
        board_name: public Ashby board name (e.g. "ashby", "linear").
    """

    BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"

    @classmethod
    def is_config_valid(cls, config: dict) -> bool:
        return bool(config.get("board_name"))

    def fetch(self) -> Iterable[dict]:
        board = self.source.config.get("board_name") if self.source else ""
        if not board:
            raise ValueError("Ashby board_name is required in source config")

        url = f"{self.BASE_URL}/{board}"
        response = fetch_url(url, timeout=30, wait=self.options.get("wait", False))
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Ashby API returned unexpected response shape")
        jobs = payload.get("jobs", [])
        if not isinstance(jobs, list):
            raise ValueError("Ashby API returned unexpected jobs shape")
        for job in jobs:
            yield job
        self.fetch_complete = True

    def normalize(self, raw: dict) -> dict:
        return {
            "title": normalize_text(raw.get("title", "")),
            "company": normalize_text(self.source.name if self.source else ""),
            "description": strip_html(
                normalize_text(
                    raw.get("descriptionHtml", raw.get("descriptionPlain", ""))
                )
            ),
            "location": normalize_text(raw.get("location", "")),
            "industry": normalize_text(raw.get("department", "")),
            "source_url": normalize_url(
                raw.get("applyUrl", raw.get("jobUrl", ""))
            ),
            "external_id": normalize_external_id(raw.get("id")),
            "employment_type": _map_employment_type(raw.get("employmentType", "")),
            "work_arrangement": normalize_work_arrangement(
                raw.get("workplaceType", "")
            ),
            "seniority": normalize_seniority(raw.get("title", "")),
            "salary_min": None,
            "salary_max": None,
            "salary_currency": "USD",
            "salary_period": "",
            "date_posted": normalize_date(raw.get("publishedAt")),
            "application_deadline": None,
            "_source_name": self.source.name,
            "raw_data": raw,
        }
