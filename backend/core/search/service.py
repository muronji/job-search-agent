"""Search orchestration service."""
from core.search.intent import FakeProvider
from core.search.query import intent_to_queryset


def search_jobs(query_text: str):
    """Parse *query_text* and return a matching Job queryset."""
    provider = FakeProvider()
    intent = provider.parse_intent(query_text)
    return intent_to_queryset(intent)


def search_jobs_from_intent(intent):
    """Return a Job queryset for the given SearchIntent."""
    return intent_to_queryset(intent)
