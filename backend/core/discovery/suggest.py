"""LLM-powered employer suggestion service.

All suggestions are unverified claims. The LLM receives only redacted profile
data (skills, titles, industries, countries) and returns company names only.
No URLs, board names, or job listings are accepted from the model.
"""
import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from core.llm import LLMError, LLMProvider, get_llm_provider
from core.llm.budget import LLMBudgetError, check_llm_budget, log_llm_usage
from core.models import EmployerCandidate, UserProfile


class SuggestionError(Exception):
    """Raised when employer suggestion fails for any reason."""


MAX_SUGGESTIONS = 25
MAX_REASON_LENGTH = 500
SUGGESTION_CACHE_TTL_SECONDS = 24 * 60 * 60
REFRESH_COOLDOWN_SECONDS = 24 * 60 * 60


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _build_prompt_inputs(profile: UserProfile) -> dict:
    """Return only the redacted, non-PII inputs used for the prompt."""
    parsed = profile.parsed_profile or {}
    preferences = parsed.get('preferences', {}) or {}
    return {
        'skills': parsed.get('skills', [])[:50],
        'job_titles': preferences.get('job_titles', [])[:20],
        'industries': preferences.get('industries', [])[:20],
        'countries': preferences.get('locations', [])[:20],
        'headline': (parsed.get('headline', '') or '')[:200],
        'summary': (parsed.get('summary', '') or '')[:1000],
    }


def _hash_inputs(inputs: dict) -> str:
    canonical = json.dumps(inputs, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]


def _cache_key(user_id: int, inputs_hash: str) -> str:
    return f'employer-suggestions:{user_id}:{inputs_hash}'


def _refresh_key(user_id: int) -> str:
    return f'employer-suggestions-refresh:{user_id}'


def _normalize_name(name: str) -> str:
    """Normalize a company name for deduplication and board probing."""
    return ''.join(c for c in name.lower() if c.isalnum())[:100]


def _build_prompt(inputs: dict) -> tuple[str, str]:
    system = (
        "You are a career research assistant. Given a candidate's redacted "
        "professional profile, suggest real employers that might hire for similar "
        "roles. Return ONLY a JSON object matching the requested schema. "
        "Do not include markdown, explanations, or any text outside the JSON. "
        "Output company names only. Never output URLs, board names, job titles, "
        "or claims that a vacancy exists."
    )
    user = (
        "Suggest up to 25 employer names for a job seeker with this profile:\n\n"
        f"{json.dumps(inputs, indent=2)}\n\n"
        "Return JSON with a single field:\n"
        "- employers: list of strings (company names only)\n\n"
        "Mix local employers in the listed countries with global or "
        "remote-friendly companies that hire in Africa and likely use "
        "Greenhouse, Lever, or Ashby job boards. Names only. No URLs."
    )
    return system, user


def _validate_response(content: str) -> list[str]:
    """Parse and validate the LLM response, returning a list of names."""
    from rest_framework import serializers

    class _EmployerNameSerializer(serializers.Serializer):
        employers = serializers.ListField(
            child=serializers.CharField(max_length=100),
            max_length=MAX_SUGGESTIONS,
        )

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SuggestionError(f"Model returned invalid JSON: {exc}") from exc

    serializer = _EmployerNameSerializer(data=data)
    if not serializer.is_valid():
        raise SuggestionError(f"Invalid suggestion shape: {serializer.errors}")
    return serializer.validated_data['employers']


def _store_candidates(user, names: list[str]) -> list[EmployerCandidate]:
    """Create or update EmployerCandidate rows for the suggested names."""
    seen = set()
    candidates = []
    for name in names:
        normalized = _normalize_name(name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidate, _ = EmployerCandidate.objects.update_or_create(
            user=user,
            normalized_name=normalized,
            defaults={
                'display_name': name[:100],
                'status': EmployerCandidate.Status.SUGGESTED,
                'reason': '',
                'adapter_key': '',
                'board_value': '',
                'sample_jobs': [],
            },
        )
        candidates.append(candidate)
    return candidates


def suggest_employers(
    user,
    profile: UserProfile,
    provider: LLMProvider | None = None,
    force: bool = False,
) -> list[EmployerCandidate]:
    """Suggest employers from the user's redacted profile.

    Results are cached by a hash of the prompt inputs. A manual refresh is
    allowed at most once per day per user. Every LLM call counts against the
    shared daily budget.
    """
    if not profile.cv_parsing_consent:
        raise SuggestionError("CV parsing consent is required")

    inputs = _build_prompt_inputs(profile)
    inputs_hash = _hash_inputs(inputs)
    cache_key = _cache_key(user.id, inputs_hash)
    refresh_key = _refresh_key(user.id)

    cached = cache.get(cache_key)
    if cached is not None and not force:
        return _store_candidates(user, cached)

    if force:
        last_refresh = cache.get(refresh_key)
        if last_refresh is not None:
            elapsed = (timezone.now() - last_refresh).total_seconds()
            remaining = REFRESH_COOLDOWN_SECONDS - elapsed
            if remaining > 0:
                raise SuggestionError(
                    f"Manual refresh allowed once per day. Wait {int(remaining)} seconds."
                )

    system_prompt, user_prompt = _build_prompt(inputs)
    estimated_input = _estimate_tokens(system_prompt + user_prompt)
    estimated_output = 500
    try:
        check_llm_budget(user, estimated_input, estimated_output)
    except LLMBudgetError as exc:
        raise SuggestionError(str(exc)) from exc

    try:
        provider = get_llm_provider(provider)
        result = provider.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format={"type": "json_object"},
        )
    except LLMError as exc:
        raise SuggestionError(f"LLM call failed: {exc}") from exc

    usage = result.get('usage', {})
    log_llm_usage(
        user,
        getattr(settings, 'KIMI_MODEL', 'kimi-k2.6'),
        usage.get('prompt_tokens', estimated_input),
        usage.get('completion_tokens', estimated_output),
    )

    names = _validate_response(result['content'])
    cache.set(cache_key, names, timeout=SUGGESTION_CACHE_TTL_SECONDS)
    if force:
        cache.set(refresh_key, timezone.now(), timeout=REFRESH_COOLDOWN_SECONDS)

    return _store_candidates(user, names)
