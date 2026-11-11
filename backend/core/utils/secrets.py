"""Utilities for detecting secret-like keys in JSON configuration."""


# Tokens that must never appear as keys in public config objects.
_FORBIDDEN_TOKENS = frozenset({
    "apikey",
    "apitoken",
    "accesskey",
    "accesstoken",
    "authkey",
    "authtoken",
    "authorization",
    "credential",
    "password",
    "privatekey",
    "secret",
    "token",
})


def normalize_key(key: str) -> str:
    """Normalize a config key for secret detection.

    Examples:
        apiKey, API_KEY, api-key, api key -> apikey
    """
    normalized = str(key).lower()
    for separator in ("_", "-", " "):
        normalized = normalized.replace(separator, "")
    # Strip camelCase boundaries by lowering capitals.
    result = []
    for i, ch in enumerate(normalized):
        if i > 0 and ch.isupper():
            result.append(ch.lower())
        else:
            result.append(ch)
    return "".join(result)


def scan_for_secrets(value):
    """Recursively scan *value* for forbidden config keys.

    Returns a list of offending key strings. Returns an empty list if no
    secret-like keys are found.
    """
    offending = []
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = normalize_key(key)
            if normalized in _FORBIDDEN_TOKENS:
                offending.append(key)
            offending.extend(scan_for_secrets(nested))
    elif isinstance(value, list):
        for item in value:
            offending.extend(scan_for_secrets(item))
    return offending
