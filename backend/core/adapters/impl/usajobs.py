"""Adapter for the USAJobs public API."""
import os
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


class UsaJobsAdapter(BaseAdapter):
    """Fetch federal job listings from USAJobs.

    Requires the ``USAJOBS_API_KEY`` environment variable.
    """

    BASE_URL = "https://data.usajobs.gov/api/search"

    @classmethod
    def is_config_valid(cls, config: dict) -> bool:
        return True

    def fetch(self) -> Iterable[dict]:
        api_key = os.environ.get("USAJOBS_API_KEY")
        if not api_key:
            raise ValueError("USAJOBS_API_KEY environment variable is required")

        params = {
            "Keyword": self.options.get("keywords", ""),
            "LocationName": self.options.get("location", ""),
            "ResultsPerPage": 100,
        }
        headers = {
            "Authorization-Key": api_key,
            "User-Agent": "job-search-agent",
            "Accept": "application/json",
        }

        page = 1
        while True:
            params["Page"] = page
            response = fetch_url(
                self.BASE_URL,
                params=params,
                headers=headers,
                timeout=30,
                wait=self.options.get("wait", False),
            )
            data = response.json()
            results = data.get("SearchResult", {}).get("SearchResultItems", [])
            if not results:
                break
            for item in results:
                yield item
            page += 1

        self.fetch_complete = True

    def normalize(self, raw: dict) -> dict:
        matched = raw.get("MatchedObjectDescriptor", {})
        title = normalize_text(matched.get("PositionTitle"))
        external_id = normalize_external_id(matched.get("PositionID"))
        locations = matched.get("PositionLocation", [])
        location = normalize_text(locations[0].get("LocationName")) if locations else ""

        salary_min, salary_max = normalize_salary({
            "min": matched.get("PositionRemuneration", [{}])[0].get("MinimumRange"),
            "max": matched.get("PositionRemuneration", [{}])[0].get("MaximumRange"),
        })

        return {
            "title": title,
            "company": "U.S. Federal Government",
            "description": strip_html(normalize_text(matched.get("UserArea", {}).get("Details", {}).get("JobSummary"))),
            "location": location,
            "industry": "Government",
            "source_url": normalize_url(matched.get("PositionURI")),
            "external_id": external_id,
            "employment_type": normalize_employment_type(
                matched.get("PositionSchedule", [{}])[0].get("Name")
            ),
            "work_arrangement": normalize_work_arrangement("onsite"),
            "seniority": normalize_seniority(matched.get("UserArea", {}).get("Details", {}).get("CareerLevel")),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "USD",
            "salary_period": normalize_salary_period(
                matched.get("PositionRemuneration", [{}])[0].get("Description")
            ),
            "date_posted": normalize_date(matched.get("PublicationStartDate")),
            "application_deadline": normalize_date(matched.get("ApplicationCloseDate")),
            "_source_name": self.source.name,
            "raw_data": raw,
        }
