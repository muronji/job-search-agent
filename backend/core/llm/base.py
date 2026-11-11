"""Abstract LLM provider interface."""
from typing import Protocol


class LLMError(Exception):
    """Raised when an LLM call fails."""


class LLMProvider(Protocol):
    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict | None = None,
    ) -> dict:
        """Call the LLM and return a dict with 'content' and 'usage'.

        'content' is the parsed JSON object from the model's message content.
        'usage' is a dict with 'prompt_tokens' and 'completion_tokens'.
        """
        ...
