"""Tests for the LLM provider layer."""
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from core.llm import KimiProvider, LLMError
from core.llm.budget import LLMBudgetError, check_llm_budget, count_calls_today, log_llm_usage
from core.models import LLMUsageLog


class KimiProviderRequestTests(TestCase):
    def _fake_response(self):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [
                {"message": {"content": '{"headline": "Test"}'}}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        return resp

    @override_settings(
        KIMI_MODEL='kimi-k2.6',
        KIMI_REASONING_EFFORT='low',
        KIMI_THINKING='disabled',
    )
    @patch.dict('os.environ', {'KIMI_BASE_URL': 'https://api.moonshot.ai/v1', 'MOONSHOT_API_KEY': 'test-key'})
    @patch('core.llm.kimi.requests.Session')
    def test_k2_6_sends_thinking_disabled_and_no_temperature(self, mock_session_cls):
        mock_session = mock_session_cls.return_value
        mock_session.mount = MagicMock()
        mock_session.post.return_value = self._fake_response()
        mock_session.close = MagicMock()

        provider = KimiProvider()
        provider.complete(
            system_prompt='system',
            user_prompt='user',
            response_format={'type': 'json_object'},
        )

        self.assertTrue(mock_session.post.called)
        _, kwargs = mock_session.post.call_args
        payload = kwargs['json']
        self.assertEqual(payload['model'], 'kimi-k2.6')
        self.assertEqual(payload['thinking'], {'type': 'disabled'})
        self.assertNotIn('temperature', payload)

    @override_settings(
        KIMI_MODEL='kimi-k3.1-pro',
        KIMI_REASONING_EFFORT='low',
        KIMI_THINKING='disabled',
    )
    @patch.dict('os.environ', {'KIMI_BASE_URL': 'https://api.moonshot.ai/v1', 'MOONSHOT_API_KEY': 'test-key'})
    @patch('core.llm.kimi.requests.Session')
    def test_k3_1_sends_reasoning_effort(self, mock_session_cls):
        mock_session = mock_session_cls.return_value
        mock_session.mount = MagicMock()
        mock_session.post.return_value = self._fake_response()
        mock_session.close = MagicMock()

        provider = KimiProvider()
        provider.complete(
            system_prompt='system',
            user_prompt='user',
        )

        _, kwargs = mock_session.post.call_args
        payload = kwargs['json']
        self.assertEqual(payload['model'], 'kimi-k3.1-pro')
        self.assertEqual(payload['reasoning_effort'], 'low')
        self.assertNotIn('thinking', payload)


class LLMBudgetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='budgetuser', password='pass')

    @override_settings(KIMI_MAX_CALLS_PER_DAY=1)
    def test_shared_call_cap_blocks_second_feature(self):
        """A CV parse counts against the same cap a suggestion would use."""
        log_llm_usage(self.user, 'kimi-k2.6', 10, 5)
        self.assertEqual(count_calls_today(self.user), 1)
        with self.assertRaises(LLMBudgetError):
            check_llm_budget(self.user, 1, 1)

    @override_settings(
        KIMI_INPUT_PRICE_PER_1M=Decimal('1000'),
        KIMI_OUTPUT_PRICE_PER_1M=Decimal('1000'),
        KIMI_MAX_DOLLARS_PER_DAY=Decimal('0.10'),
    )
    def test_shared_dollar_cap_blocks_second_feature(self):
        """Dollar spending in one feature blocks another feature."""
        log_llm_usage(self.user, 'kimi-k2.6', 100, 100)
        with self.assertRaises(LLMBudgetError):
            check_llm_budget(self.user, 1000, 1000)

    @override_settings(KIMI_MAX_CALLS_PER_DAY=5)
    def test_check_llm_budget_allows_under_cap(self):
        log_llm_usage(self.user, 'kimi-k2.6', 10, 5)
        # Should not raise
        check_llm_budget(self.user, 100, 50)
