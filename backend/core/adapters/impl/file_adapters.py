"""Adapters for CSV and JSON file imports."""
import csv
import json
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


MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


def _read_text_file(path: str) -> str:
    if path is None:
        raise ValueError("File path is required")
    with open(path, "r", encoding="utf-8", newline="") as f:
        content = f.read(MAX_FILE_SIZE_BYTES + 1)
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise ValueError("File exceeds 10 MB limit")
    return content


class CsvAdapter(BaseAdapter):
    """Import jobs from a CSV file."""

    @classmethod
    def is_config_valid(cls, config: dict) -> bool:
        return True

    def fetch(self) -> Iterable[dict]:
        path = self.options.get("file")
        content = _read_text_file(path)
        reader = csv.DictReader(content.splitlines())
        for row in reader:
            yield dict(row)
        self.fetch_complete = True

    def normalize(self, raw: dict) -> dict:
        base_url = self.source.base_url if self.source else ""
        return {
            "title": normalize_text(raw.get("title")),
            "company": normalize_text(raw.get("company")),
            "description": strip_html(normalize_text(raw.get("description"))),
            "location": normalize_text(raw.get("location")),
            "industry": normalize_text(raw.get("industry")),
            "source_url": normalize_url(raw.get("source_url"), base_url),
            "external_id": normalize_external_id(raw.get("external_id")),
            "employment_type": normalize_employment_type(raw.get("employment_type")),
            "work_arrangement": normalize_work_arrangement(raw.get("work_arrangement")),
            "seniority": normalize_seniority(raw.get("seniority")),
            "salary_min": normalize_salary(raw.get("salary"))[0],
            "salary_max": normalize_salary(raw.get("salary"))[1],
            "salary_currency": normalize_text(raw.get("salary_currency", "USD")),
            "salary_period": normalize_salary_period(raw.get("salary_period")),
            "date_posted": normalize_date(raw.get("date_posted")),
            "application_deadline": normalize_date(raw.get("application_deadline")),
            "_source_name": normalize_text(raw.get("source_name") or self.source.name),
            "raw_data": raw,
        }


class JsonAdapter(BaseAdapter):
    """Import jobs from a JSON file or string."""

    @classmethod
    def is_config_valid(cls, config: dict) -> bool:
        return True

    def fetch(self) -> Iterable[dict]:
        path = self.options.get("file")
        content = _read_text_file(path)
        data = json.loads(content)
        if isinstance(data, dict):
            data = data.get("jobs", data.get("items", data.get("results", [data])))
        if not isinstance(data, list):
            raise ValueError("JSON must contain a list of jobs")
        for item in data:
            yield item
        self.fetch_complete = True

    def normalize(self, raw: dict) -> dict:
        base_url = self.source.base_url if self.source else ""
        salary_min, salary_max = normalize_salary(raw.get("salary"))
        return {
            "title": normalize_text(raw.get("title")),
            "company": normalize_text(raw.get("company")),
            "description": strip_html(normalize_text(raw.get("description"))),
            "location": normalize_text(raw.get("location")),
            "industry": normalize_text(raw.get("industry")),
            "source_url": normalize_url(raw.get("source_url"), base_url),
            "external_id": normalize_external_id(raw.get("external_id")),
            "employment_type": normalize_employment_type(raw.get("employment_type")),
            "work_arrangement": normalize_work_arrangement(raw.get("work_arrangement")),
            "seniority": normalize_seniority(raw.get("seniority")),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": normalize_text(raw.get("salary_currency", "USD")),
            "salary_period": normalize_salary_period(raw.get("salary_period")),
            "date_posted": normalize_date(raw.get("date_posted")),
            "application_deadline": normalize_date(raw.get("application_deadline")),
            "_source_name": normalize_text(raw.get("source_name") or self.source.name),
            "raw_data": raw,
        }
