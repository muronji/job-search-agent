"""Adapter for the Lever public Postings API."""
from typing import Iterable

from core.adapters.base import BaseAdapter
from core.adapters.normalizers import (
    normalize_date,
    normalize_employment_type,
    normalize_external_id,
    normalize_salary,
    normalize_seniority,
    normalize_text,
    normalize_url,
    normalize_work_arrangement,
    strip_html,
)
from core.utils.http import fetch_url


class LeverAdapter(BaseAdapter):
    """Fetch public job postings from a Lever job board.

    Config:
        board_token: public Lever site name (e.g. "lever", "stripe").
    """

    BASE_URL = "https://api.lever.co/v0/postings"

    @classmethod
    def is_config_valid(cls, config: dict) -> bool:
        return bool(config.get("board_token"))

    def fetch(self) -> Iterable[dict]:
        site = self.source.config.get("board_token") if self.source else ""
        if not site:
            raise ValueError("Lever board_token is required in source config")

        url = f"{self.BASE_URL}/{site}?mode=json"
        response = fetch_url(url, timeout=30, wait=self.options.get("wait", False))
        postings = response.json()
        if not isinstance(postings, list):
            raise ValueError("Lever API returned unexpected response shape")
        for posting in postings:
            yield posting
        self.fetch_complete = True

    def normalize(self, raw: dict) -> dict:
        categories = raw.get("categories", {}) or {}
        salary_min, salary_max = normalize_salary(raw.get("description", ""))
        return {
            "title": normalize_text(raw.get("text", "")),
            "company": normalize_text(self.source.name if self.source else ""),
            "description": strip_html(normalize_text(raw.get("description", ""))),
            "location": normalize_text(categories.get("location", "")),
            "industry": normalize_text(categories.get("team", "")),
            "source_url": normalize_url(raw.get("hostedUrl", "")),
            "external_id": normalize_external_id(raw.get("id")),
            "employment_type": normalize_employment_type(categories.get("commitment", "")),
            "work_arrangement": normalize_work_arrangement(categories.get("workplaceType", "")),
            "seniority": normalize_seniority(categories.get("level", "")),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "USD",
            "salary_period": "",
            "date_posted": normalize_date(raw.get("createdAt")),
            "application_deadline": None,
            "_source_name": self.source.name,
            "raw_data": raw,
        }
