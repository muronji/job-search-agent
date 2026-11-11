"""Kimi API Platform provider, OpenAI-compatible, plain HTTP, no SDK."""
import json
import os

import requests
from django.conf import settings

from core.utils.ssrf import SafeHTTPAdapter

from .base import LLMError, LLMProvider


class KimiProvider:
    """Production provider for the Kimi API Platform.

    This is the single allowed exception to the generic ``fetch_url`` rule: the
    destination is a fixed, operator-configured base URL rather than a
    user-supplied or discovered URL. It still uses the SSRF-safe adapter so DNS
    rebinding and private addresses are rejected.
    """

    def __init__(self):
        self.base_url = os.environ.get('KIMI_BASE_URL', '').rstrip('/')
        self.api_key = os.environ.get('MOONSHOT_API_KEY', '')
        self.model = getattr(settings, 'KIMI_MODEL', 'kimi-k2.6')
        self.reasoning_effort = getattr(settings, 'KIMI_REASONING_EFFORT', 'low')
        self.thinking = getattr(settings, 'KIMI_THINKING', 'disabled')
        self.timeout = 60

        if not self.base_url:
            raise LLMError("KIMI_BASE_URL is not set")
        if not self.api_key:
            raise LLMError("MOONSHOT_API_KEY is not set")

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict | None = None,
    ) -> dict:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 4096,
        }
        if response_format:
            payload["response_format"] = response_format
        if self._supports_reasoning_effort():
            payload["reasoning_effort"] = self.reasoning_effort
        if self._supports_thinking():
            payload["thinking"] = {"type": self.thinking}

        session = requests.Session()
        session.mount("http://", SafeHTTPAdapter())
        session.mount("https://", SafeHTTPAdapter())
        try:
            resp = session.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise LLMError(f"Kimi request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"Kimi returned invalid JSON: {exc}") from exc
        finally:
            session.close()

        try:
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected Kimi response shape: {exc}") from exc

        if not isinstance(content, str):
            raise LLMError("Kimi response content is not a string")

        return {
            "content": content,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
        }

    def _supports_reasoning_effort(self) -> bool:
        return "k3.1" in self.model.lower()

    def _supports_thinking(self) -> bool:
        return "k2.6" in self.model.lower()
