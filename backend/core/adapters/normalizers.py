"""Normalizers for converting raw source records into Job model fields."""
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

from dateutil import parser as date_parser
from django.utils.dateparse import parse_date

from core.models import Job
from core.utils.ssrf import SSRFError, validate_url


def normalize_text(value):
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"\s+", " ", text.strip())
    return text


def normalize_external_id(value):
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped if stripped else None


def normalize_url(value, base_url: str = ""):
    if not value:
        return ""
    text = str(value).strip()
    if base_url and not urlparse(text).netloc:
        text = urljoin(base_url, text)
    try:
        validate_url(text)
    except SSRFError:
        return ""
    return text


def normalize_date(value):
    if value is None or value == "":
        return None
    # Django's parse_date handles ISO 8601 dates.
    parsed = parse_date(str(value))
    if parsed is not None:
        return parsed
    try:
        dt = date_parser.parse(str(value))
        return dt.date()
    except (ValueError, TypeError, OverflowError):
        return None


def normalize_datetime(value):
    if value is None or value == "":
        return None
    from django.utils.dateparse import parse_datetime
    parsed = parse_datetime(str(value))
    if parsed is not None:
        return parsed
    try:
        return date_parser.parse(str(value))
    except (ValueError, TypeError, OverflowError):
        return None


def _parse_money(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    # Remove currency symbols, commas, spaces.
    text = re.sub(r"[^\d.\-]", "", str(value))
    if not text:
        return None
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def normalize_salary(value):
    """Return (salary_min, salary_max) tuple or (None, None) if unparseable."""
    if value is None or value == "":
        return None, None
    if isinstance(value, dict):
        min_val = _parse_money(value.get("min") or value.get("min_amount"))
        max_val = _parse_money(value.get("max") or value.get("max_amount"))
        return min_val, max_val
    # Try to detect ranges like "$50,000 - $70,000" or "50000-70000".
    text = str(value)
    numbers = re.findall(r"[\d,]+(?:\.\d+)?", text)
    if len(numbers) >= 2:
        return _parse_money(numbers[0]), _parse_money(numbers[1])
    if len(numbers) == 1:
        amount = _parse_money(numbers[0])
        return amount, amount
    return None, None


def _choice_value(choices, value):
    if not value:
        return ""
    text = str(value).lower().strip()
    for key, label in choices:
        if text == key or text == str(label).lower().strip():
            return key
    return ""


def normalize_employment_type(value):
    return _choice_value(Job.EmploymentType.choices, value)


def normalize_work_arrangement(value):
    return _choice_value(Job.WorkArrangement.choices, value)


def normalize_seniority(value):
    return _choice_value(Job.Seniority.choices, value)


def normalize_salary_period(value):
    return _choice_value(Job.SalaryPeriod.choices, value)


def strip_html(value):
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    return re.sub(r"\s+", " ", text).strip()
