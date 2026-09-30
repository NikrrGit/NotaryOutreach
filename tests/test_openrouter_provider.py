"""OpenRouter request contracts and failure handling; no live requests."""

import json
import unittest
from unittest.mock import patch

import httpx
from openai import APIStatusError, OpenAI

from agents.email_writer import EmailDraft
from providers.clients import OpenRouterProvider
from providers.errors import failure_message
from providers.settings import provider_settings


class OpenRouterTests(unittest.TestCase):
    def test_one_key_defaults_overrides_and_separate_search(self):
        values = {"LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "test-key"}
        for search in (False, True):
            settings = provider_settings(values, search=search, require_key=True)
            self.assertEqual(settings.base_url, "https://openrouter.ai/api/v1")
            self.assertEqual(settings.model, "openai/gpt-4.1-mini")
            self.assertEqual(settings.search_model, settings.model)
            custom = {**values, "LLM_MODEL": "anthropic/claude-sonnet-4"}
            self.assertEqual(provider_settings(custom, search=search).search_model, custom["LLM_MODEL"])
            custom["SEARCH_MODEL"] = "openai/gpt-4.1-mini"
            self.assertEqual(provider_settings(custom, search=search).search_model, custom["SEARCH_MODEL"])
        mixed = provider_settings({**values, "LLM_PROVIDER": "anthropic", "LLM_MODEL": "claude-sonnet-4-6",
                                   "SEARCH_PROVIDER": "openrouter"}, search=True, require_key=True)
        self.assertEqual(mixed.model, "openai/gpt-4.1-mini")
        self.assertEqual(mixed.api_key, "test-key")
        with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
            provider_settings({"LLM_PROVIDER": "openrouter", "OPENAI_API_KEY": "wrong-key"}, require_key=True)
        with self.assertRaisesRegex(ValueError, "Remove :online"):
            provider_settings({**values, "LLM_MODEL": "openai/gpt-4.1-mini:online"})

    def test_unsourced_empty_and_incomplete_research_never_reaches_formatter(self):
        cases = [
            ("stop", "Unsourced", None),
            ("stop", "Unsourced", []),
            ("stop", " ", []),
            ("length", "Partial", []),
            ("tool_calls", "Needs a tool", []),
        ]
        for finish, content, annotations in cases:
            with self.subTest(finish=finish, content=content):
                data = {"id": "test", "object": "chat.completion", "created": 1, "model": "test",
                        "choices": [{"index": 0, "finish_reason": finish, "message": {
                            "role": "assistant", "content": content, "annotations": annotations}}]}
                with OpenAI(api_key="test", base_url="https://openrouter.ai/api/v1", http_client=httpx.Client(
                    transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data)),
                )) as client:
                    provider = OpenRouterProvider(provider_settings({"LLM_PROVIDER": "openrouter"}), client)
                    with patch.object(provider, "_generate") as formatter, self.assertRaises(RuntimeError):
                        provider.search(system_prompt="Discover contacts", prompt="Berlin")
                    formatter.assert_not_called()

    def test_http_errors_propagate_with_safe_actionable_messages(self):
        for status, expected in ((400, "rejected the request"), (401, "rejected access"),
                                 (402, "credits"), (429, "usage limit")):
            with self.subTest(status=status), OpenAI(
                api_key="test", base_url="https://openrouter.ai/api/v1", max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
                    status, json={"error": {"message": "private-provider-body", "code": status}},
                ))),
            ) as client:
                provider = OpenRouterProvider(provider_settings({"LLM_PROVIDER": "openrouter"}), client)
                with self.assertRaises(APIStatusError) as caught:
                    provider.search(system_prompt="Discover", prompt="Berlin")
                self.assertIn(expected, failure_message(caught.exception))
                self.assertNotIn("private-provider-body", failure_message(caught.exception))

    def test_generation_uses_model_override_and_validates_json_locally(self):
        requests = []
        responses = iter(['not json', '{}', '{"subject":"Hello","body":"A short email"}'])

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1,
                "model": "test", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": next(responses)}}]})

        settings = provider_settings({"LLM_PROVIDER": "openrouter", "LLM_MODEL": "anthropic/claude-sonnet-4"})
        with OpenAI(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
            provider = OpenRouterProvider(settings, client)
            for _ in range(2):
                with self.assertRaises(ValueError):
                    provider.generate_structured(system_prompt="Write", user_prompt="Email", response_model=EmailDraft)
            result = provider.generate_structured(system_prompt="Write", user_prompt="Email", response_model=EmailDraft)
        self.assertEqual(result.subject, "Hello")
        self.assertTrue(all(request["model"] == settings.model for request in requests))
        self.assertTrue(all("plugins" not in request and "response_format" not in request for request in requests))
