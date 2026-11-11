"""Local PII redaction before any text is sent to an LLM."""
import re


EMAIL_RE = re.compile(
    r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
)
PHONE_RE = re.compile(
    r'(?:\+?\d{1,3}[-.\s]?)?(?:\(\d{1,4}\)|\d{1,4})[-.\s]?\d{1,4}[-.\s]?\d{1,9}'
)
URL_RE = re.compile(r'https?://\S+')
DOMAIN_RE = re.compile(
    r'\b(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/[a-zA-Z0-9_./-]*)?\b',
    re.IGNORECASE,
)
HANDLE_RE = re.compile(r'@\w+')
STREET_RE = re.compile(
    r'\b\d+\s+(?:[A-Za-z]+\s*){1,4}(?:street|st|avenue|ave|road|rd|'
    r'boulevard|blvd|lane|ln|drive|dr|way|court|ct|place|pl|circle|cir)'
    r'\b',
    re.IGNORECASE,
)
NAME_RE = re.compile(r'^[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,3}$')
NAME_LABEL_RE = re.compile(
    r'(\bname\s*[:：]\s*)([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})',
    re.IGNORECASE,
)
_DATE_LIKE_RE = re.compile(
    r'^(?:\d{4}\s*[-/]\s*\d{4}|\d{4}\s*[-/]\s*\d{2}|\d{2}\s*[-/]\s*\d{4})$'
)
_MONTH_NAMES = {
    'jan', 'feb', 'mar', 'apr', 'may', 'jun',
    'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
}


def _is_phone_number(candidate: str) -> bool:
    """Return True if *candidate* looks like a phone number, not a date."""
    digits = sum(ch.isdigit() for ch in candidate)
    if digits < 7 or digits > 15:
        return False
    if any(month in candidate.lower() for month in _MONTH_NAMES):
        return False
    core = re.sub(r'[()\[\]{}]', '', candidate).strip()
    if _DATE_LIKE_RE.match(core):
        return False
    return True


def _phone_replacer(match: re.Match) -> str:
    candidate = match.group(0)
    if _is_phone_number(candidate):
        return '[REDACTED_PHONE]'
    return candidate


def redact_pii(text: str) -> str:
    """Return *text* with email, phone, URL, handle, address and name redacted.

    City and country names are left intact because they are useful for job
    matching and are not considered direct PII in this context.
    """
    lines = text.splitlines()
    redacted_lines = []
    # Only guess at a name in the first few lines of a CV.
    name_scan_limit = min(5, len(lines))

    for i, line in enumerate(lines):
        stripped = line.strip()
        if i < name_scan_limit and NAME_RE.match(stripped) and len(stripped) < 50:
            line = '[REDACTED_NAME]'
        else:
            line = NAME_LABEL_RE.sub(r'\1[REDACTED_NAME]', line)
            line = STREET_RE.sub('[REDACTED_ADDRESS]', line)
            line = URL_RE.sub('[REDACTED_URL]', line)
            line = EMAIL_RE.sub('[REDACTED_EMAIL]', line)
            line = DOMAIN_RE.sub('[REDACTED_URL]', line)
            line = HANDLE_RE.sub('[REDACTED_HANDLE]', line)
            line = PHONE_RE.sub(_phone_replacer, line)
        redacted_lines.append(line)

    return '\n'.join(redacted_lines)


def redaction_counts(redacted_text: str) -> dict:
    """Return counts of each redaction placeholder in *redacted_text*."""
    return {
        'name_count': redacted_text.count('[REDACTED_NAME]'),
        'email_count': redacted_text.count('[REDACTED_EMAIL]'),
        'phone_count': redacted_text.count('[REDACTED_PHONE]'),
        'url_count': redacted_text.count('[REDACTED_URL]'),
        'handle_count': redacted_text.count('[REDACTED_HANDLE]'),
        'address_count': redacted_text.count('[REDACTED_ADDRESS]'),
    }
