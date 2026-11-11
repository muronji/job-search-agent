"""LLM provider factory."""
from .base import LLMError, LLMProvider
from .fake import FakeProvider
from .kimi import KimiProvider


def get_llm_provider(provider: LLMProvider | None = None) -> LLMProvider:
    """Return the requested provider, or the production Kimi provider.

    The real provider is never selected by a settings flag; callers must pass
    an explicit provider instance (the fake provider in tests).
    """
    if provider is not None:
        return provider
    return KimiProvider()


__all__ = ["LLMError", "LLMProvider", "FakeProvider", "KimiProvider", "get_llm_provider"]
