"""Adapter for RSS/Atom job feeds."""
from typing import Iterable

import feedparser

from core.adapters.base import BaseAdapter
from core.adapters.normalizers import (
    normalize_date,
    normalize_external_id,
    normalize_salary,
    normalize_text,
    normalize_url,
    strip_html,
)
from core.utils.http import fetch_url


class RssAdapter(BaseAdapter):
    """Fetch job listings from an RSS or Atom feed.

    Options:
        feed_url: URL of the feed.
    """

    @classmethod
    def is_config_valid(cls, config: dict) -> bool:
        return True

    def fetch(self) -> Iterable[dict]:
        feed_url = self.options.get("feed_url")
        if not feed_url:
            raise ValueError("feed_url is required")

        response = fetch_url(feed_url, timeout=30, wait=self.options.get("wait", False))
        parsed = feedparser.parse(response.content)
        for entry in parsed.entries:
            yield {
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", entry.get("description", "")),
                "published": entry.get("published", entry.get("updated", "")),
            }
        self.fetch_complete = True

    def normalize(self, raw: dict) -> dict:
        base_url = self.source.base_url if self.source else ""
        salary_min, salary_max = normalize_salary(raw.get("summary"))
        return {
            "title": normalize_text(raw.get("title")),
            "company": "",
            "description": strip_html(normalize_text(raw.get("summary"))),
            "location": "",
            "industry": "",
            "source_url": normalize_url(raw.get("link"), base_url),
            "external_id": normalize_external_id(raw.get("link")),
            "employment_type": "",
            "work_arrangement": "",
            "seniority": "",
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "USD",
            "salary_period": "",
            "date_posted": normalize_date(raw.get("published")),
            "application_deadline": None,
            "_source_name": self.source.name,
            "raw_data": raw,
        }
