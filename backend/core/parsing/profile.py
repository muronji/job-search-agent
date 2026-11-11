"""Parse a redacted CV into an editable structured profile."""
import json

from django.conf import settings

from core.llm import LLMError, LLMProvider, get_llm_provider
from core.llm.budget import LLMBudgetError, check_llm_budget, log_llm_usage
from core.models import UserProfile
from core.serializers import ParsedProfileSerializer

from .extractors import ExtractionError, extract_text_from_file
from .redaction import redact_pii


class ParseError(Exception):
    """Raised when CV parsing fails for any reason."""


MAX_RETRIES = 1


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _build_prompt(redacted_text: str) -> tuple[str, str]:
    system = (
        "You are a CV parser. Extract the candidate's professional profile "
        "from the redacted CV text and return ONLY a JSON object matching the "
        "requested schema. Do not include markdown, explanations, or any text "
        "outside the JSON. Use simple fields only; no nested references or oneOf."
    )
    user = (
        "Extract a profile from this CV:\n\n"
        f"{redacted_text}\n\n"
        "Return JSON with these fields (all optional):\n"
        "- headline (string)\n"
        "- summary (string)\n"
        "- location (city/country only)\n"
        "- skills (list of strings)\n"
        "- experience (list of {title, company, location, start_date, end_date, description})\n"
        "- education (list of {institution, degree, field, start_date, end_date})\n"
        "- preferences (object with job_titles, industries, locations, "
        "employment_types, work_arrangements, seniority_levels, min_salary)\n"
        "Do not include contact emails, phone numbers, URLs, or social handles."
    )
    return system, user


def _validate_and_save(profile: UserProfile, raw_json: dict) -> UserProfile:
    serializer = ParsedProfileSerializer(data=raw_json)
    if not serializer.is_valid():
        raise ParseError(f"Invalid profile shape: {serializer.errors}")
    profile.parsed_profile = serializer.validated_data
    profile.save(update_fields=['parsed_profile', 'updated_at'])
    return profile


def parse_cv(profile: UserProfile, provider: LLMProvider | None = None) -> UserProfile:
    """Extract and parse the user's CV into an editable profile."""
    if not profile.cv:
        raise ParseError("No CV file uploaded")
    if not profile.cv_parsing_consent:
        raise ParseError("CV parsing consent is required")

    try:
        raw_text = extract_text_from_file(profile.cv.path)
    except ExtractionError as exc:
        raise ParseError(str(exc)) from exc

    redacted = redact_pii(raw_text)
    profile.redacted_text = redacted
    profile.save(update_fields=['redacted_text', 'updated_at'])

    estimated_input = _estimate_tokens(redacted)
    estimated_output = 500  # conservative initial estimate
    try:
        check_llm_budget(profile.user, estimated_input, estimated_output)
    except LLMBudgetError as exc:
        raise ParseError(str(exc)) from exc

    provider = get_llm_provider(provider)
    system_prompt, user_prompt = _build_prompt(redacted)

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            result = provider.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format={"type": "json_object"},
            )
        except LLMError as exc:
            raise ParseError(f"LLM call failed: {exc}") from exc

        usage = result.get('usage', {})
        log_llm_usage(
            profile.user,
            getattr(settings, 'KIMI_MODEL', 'kimi-k2.6'),
            usage.get('prompt_tokens', estimated_input),
            usage.get('completion_tokens', estimated_output),
        )

        try:
            raw_json = json.loads(result['content'])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            last_error = f"Model returned non-JSON content: {exc}"
            user_prompt = (
                "Your previous response was not valid JSON. "
                "Return ONLY a JSON object matching the schema.\n\n" + user_prompt
            )
            continue

        try:
            return _validate_and_save(profile, raw_json)
        except ParseError as exc:
            last_error = str(exc)
            user_prompt = (
                f"Your previous response failed validation: {last_error}. "
                f"Return ONLY a corrected JSON object.\n\n" + user_prompt
            )

    raise ParseError(f"Could not parse CV after retries: {last_error}")


def preview_redacted_text(profile: UserProfile) -> str:
    """Return the redacted CV text without any LLM call."""
    if not profile.cv:
        raise ParseError("No CV file uploaded")
    try:
        raw_text = extract_text_from_file(profile.cv.path)
    except ExtractionError as exc:
        raise ParseError(str(exc)) from exc
    return redact_pii(raw_text)
