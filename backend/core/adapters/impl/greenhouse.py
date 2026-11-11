"""Adapter for the Greenhouse public job board API."""
from typing import Iterable

from core.adapters.base import BaseAdapter
from core.adapters.normalizers import (
    normalize_date,
    normalize_employment_type,
    normalize_external_id,
    normalize_salary,
    normalize_salary_period,
    normalize_seniority,
    normalize_text,
    normalize_url,
    normalize_work_arrangement,
    strip_html,
)
from core.utils.http import fetch_url


class GreenhouseAdapter(BaseAdapter):
    """Fetch job listings from a Greenhouse job board.

    Config:
        board_token: public board token (e.g. "stripe").
    """

    @classmethod
    def is_config_valid(cls, config: dict) -> bool:
        return bool(config.get("board_token"))

    def fetch(self) -> Iterable[dict]:
        board_token = self.source.config.get("board_token") if self.source else ""
        if not board_token:
            raise ValueError("Greenhouse board_token is required in source config")

        url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
        response = fetch_url(url, timeout=30, wait=self.options.get("wait", False))
        data = response.json()
        jobs = data.get("jobs", [])
        for job in jobs:
            yield job
        self.fetch_complete = True

    def normalize(self, raw: dict) -> dict:
        base_url = self.source.base_url if self.source else ""
        salary_min, salary_max = normalize_salary(raw.get("pay_range"))
        return {
            "title": normalize_text(raw.get("title")),
            "company": normalize_text(self.source.name if self.source else ""),
            "description": strip_html(normalize_text(raw.get("content"))),
            "location": normalize_text(raw.get("location", {}).get("name", "")),
            "industry": "",
            "source_url": normalize_url(raw.get("absolute_url"), base_url),
            "external_id": normalize_external_id(raw.get("id")),
            "employment_type": normalize_employment_type(raw.get("employment_type")),
            "work_arrangement": normalize_work_arrangement(raw.get("work_arrangement")),
            "seniority": normalize_seniority(raw.get("seniority")),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": normalize_text(raw.get("pay_currency", "USD")),
            "salary_period": normalize_salary_period(raw.get("pay_period")),
            "date_posted": normalize_date(raw.get("updated_at")),
            "application_deadline": None,
            "_source_name": self.source.name,
            "raw_data": raw,
        }
