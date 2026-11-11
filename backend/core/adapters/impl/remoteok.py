"""Adapter for the RemoteOK public jobs API.

Endpoint: https://remoteok.com/api
Documentation/terms for this public endpoint are unverified; use politely.
"""
from typing import Iterable

from core.adapters.base import BaseAdapter
from core.adapters.normalizers import (
    normalize_date,
    normalize_external_id,
    normalize_salary,
    normalize_text,
    normalize_url,
    strip_html,
)
from core.utils.http import can_fetch, fetch_url


class RemoteOkAdapter(BaseAdapter):
    """Fetch remote job listings from RemoteOK.

    Config: none required; the source name should be "Remote OK".
    Options: none.
    """

    API_URL = "https://remoteok.com/api"

    @classmethod
    def is_config_valid(cls, config: dict) -> bool:
        return True

    def fetch(self) -> Iterable[dict]:
        if not can_fetch(self.API_URL):
            raise ValueError("robots.txt disallows fetching RemoteOK")

        response = fetch_url(self.API_URL, timeout=30, wait=self.options.get("wait", False))
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("RemoteOK API returned unexpected response shape")

        for item in data:
            # Skip the metadata object that appears at the start of the list.
            if not isinstance(item, dict):
                continue
            if not item.get("id") or not item.get("position"):
                continue
            yield item

        self.fetch_complete = True

    def normalize(self, raw: dict) -> dict:
        salary_min, salary_max = normalize_salary(
            {
                "min": raw.get("salary_min"),
                "max": raw.get("salary_max"),
            }
        )
        description = strip_html(
            normalize_text(raw.get("description", ""))
        )
        return {
            "title": normalize_text(raw.get("position", "")),
            "company": normalize_text(raw.get("company", "")),
            "description": description,
            "location": normalize_text(raw.get("location", "Remote")),
            "industry": normalize_text(", ".join((raw.get("tags") or [])[:3])),
            "source_url": normalize_url(raw.get("url", "")),
            "external_id": normalize_external_id(str(raw.get("id", ""))),
            "employment_type": "",
            "work_arrangement": "remote",
            "seniority": "",
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "USD",
            "salary_period": "",
            "date_posted": normalize_date(raw.get("date")),
            "application_deadline": None,
            "_source_name": self.source.name,
            "raw_data": raw,
        }
