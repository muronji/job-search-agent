"""Shared LLM budget checks and usage logging.

Budgets are enforced across all LLM-powered features (CV parsing, employer
suggestions, and any later extraction) so one feature cannot starve another.
"""
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from core.models import LLMUsageLog


class LLMBudgetError(Exception):
    """Raised when an LLM call would exceed a daily budget cap."""


def check_llm_budget(user, estimated_input: int, estimated_output: int):
    """Raise LLMBudgetError if the proposed call would exceed a daily cap."""
    today = timezone.now().date()
    max_calls = getattr(settings, 'KIMI_MAX_CALLS_PER_DAY', 50)
    max_tokens = getattr(settings, 'KIMI_MAX_TOKENS_PER_DAY', 100000)
    max_dollars = getattr(settings, 'KIMI_MAX_DOLLARS_PER_DAY', Decimal('1.00'))
    input_price = getattr(settings, 'KIMI_INPUT_PRICE_PER_1M', Decimal('0'))
    output_price = getattr(settings, 'KIMI_OUTPUT_PRICE_PER_1M', Decimal('0'))

    calls_today = LLMUsageLog.objects.filter(
        user=user,
        created_at__date=today,
    ).count()
    if calls_today >= max_calls:
        raise LLMBudgetError("Daily LLM call limit reached")

    totals = LLMUsageLog.objects.filter(
        user=user,
        created_at__date=today,
    ).aggregate(
        input_total=Sum('input_tokens'),
        output_total=Sum('output_tokens'),
        cost_total=Sum('estimated_cost_usd'),
    )
    input_total = totals['input_total'] or 0
    output_total = totals['output_total'] or 0
    cost_total = totals['cost_total'] or Decimal('0')

    if input_total + estimated_input > max_tokens:
        raise LLMBudgetError("Daily input-token limit would be exceeded")
    if output_total + estimated_output > max_tokens:
        raise LLMBudgetError("Daily output-token limit would be exceeded")

    estimated_cost = (
        Decimal(estimated_input) * input_price / Decimal('1000000')
        + Decimal(estimated_output) * output_price / Decimal('1000000')
    )
    if cost_total + estimated_cost > max_dollars:
        raise LLMBudgetError("Daily dollar limit would be exceeded")


def log_llm_usage(user, model: str, input_tokens: int, output_tokens: int):
    """Record one LLM call against the user's daily budget."""
    input_price = getattr(settings, 'KIMI_INPUT_PRICE_PER_1M', Decimal('0'))
    output_price = getattr(settings, 'KIMI_OUTPUT_PRICE_PER_1M', Decimal('0'))
    cost = (
        Decimal(input_tokens) * input_price / Decimal('1000000')
        + Decimal(output_tokens) * output_price / Decimal('1000000')
    )
    LLMUsageLog.objects.create(
        user=user,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=cost,
    )


def count_calls_today(user) -> int:
    """Return the number of LLM calls already logged today for *user*."""
    today = timezone.now().date()
    return LLMUsageLog.objects.filter(user=user, created_at__date=today).count()
