"""Test-only fake LLM provider."""
import json

from .base import LLMError


class FakeProvider:
    """Deterministic fake provider for unit tests. Never touches the network."""

    def __init__(self, response_data: dict | None = None, fail: bool = False):
        self.response_data = response_data
        self.fail = fail
        self.calls = []

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict | None = None,
    ) -> dict:
        self.calls.append({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response_format": response_format,
        })
        if self.fail:
            raise LLMError("Fake provider failure")
        data = self.response_data if self.response_data is not None else {
            "headline": "Experienced Professional",
            "summary": "A short summary.",
            "location": "New York, USA",
            "skills": ["Python", "Django"],
            "experience": [],
            "education": [],
            "preferences": {
                "job_titles": ["Software Engineer"],
                "industries": ["Technology"],
                "locations": ["Remote"],
                "employment_types": ["full_time"],
                "work_arrangements": ["remote"],
                "seniority_levels": ["senior"],
                "min_salary": None,
            },
        }
        return {
            "content": json.dumps(data),
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
