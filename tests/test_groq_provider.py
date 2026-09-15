"""Offline checks for the shared Groq provider."""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.email_writer import EmailDraft, EmailWriter, EmailWriterInput
from providers.groq import GroqProvider


def completion(content, finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason, message=SimpleNamespace(content=content),
    )])


class GroqProviderTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("providers.groq.Groq")
        self.groq = patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = GroqProvider(api_key="test-key")
        self.create = self.groq.return_value.chat.completions.create

    def test_constructor_stores_models_and_key(self):
        self.assertEqual(self.provider.compound_model, "groq/compound")
        self.assertEqual(self.provider.reasoning_model, "openai/gpt-oss-20b")
        self.assertEqual(self.groq.call_args.kwargs["api_key"], "test-key")
        custom = GroqProvider("other-key", "search-model", "reasoning-model")
        self.assertEqual(custom.compound_model, "search-model")
        self.assertEqual(custom.reasoning_model, "reasoning-model")

    def test_environment_key_and_missing_key(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "env-key"}, clear=True):
            GroqProvider()
            self.assertEqual(self.groq.call_args.kwargs["api_key"], "env-key")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "GROQ_API_KEY"):
                GroqProvider()

    def test_email_writer_can_use_provider(self):
        self.create.return_value = completion(json.dumps({
            "subject": "Terminanfrage", "body": "Wann ist ein Termin möglich?",
        }))
        data = EmailWriterInput(
            notary_name="Test", city="Berlin", company_type="UG",
            verification_reason="Confirmed", evidence="UG-Gründung",
            source_url="https://example.com",
        )
        draft = EmailWriter(self.provider).writer(data)
        self.assertIsInstance(draft, EmailDraft)
        self.assertEqual(draft.language, "de")
        request = self.create.call_args.kwargs
        self.assertEqual(request["model"], "openai/gpt-oss-20b")
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertEqual(request["response_format"]["json_schema"]["schema"],
                         EmailDraft.model_json_schema())
        self.assertNotIn("compound_custom", request)

    def structured(self):
        return self.provider.generate_structured(
            system_prompt="Write JSON", user_prompt="Draft", response_model=EmailDraft,
        )

    def test_invalid_structured_results_are_rejected(self):
        for content in ('not json', '[]', '{}', '{"subject":"","body":"text"}'):
            with self.subTest(content=content):
                self.create.return_value = completion(content)
                with self.assertRaises(ValidationError):
                    self.structured()

    def test_invalid_response_model_is_rejected_before_api_call(self):
        with self.assertRaises(TypeError):
            self.provider.generate_structured(
                system_prompt="", user_prompt="", response_model=dict,
            )
        self.create.assert_not_called()

    def test_empty_and_incomplete_responses_are_rejected(self):
        for response in (SimpleNamespace(choices=[]), completion(None),
                         completion("  "), completion('{}', "length")):
            with self.subTest(response=response):
                self.create.return_value = response
                with self.assertRaises(RuntimeError):
                    self.structured()

    def test_api_errors_propagate(self):
        self.create.side_effect = TimeoutError("timeout")
        with self.assertRaises(TimeoutError):
            self.structured()

    def test_existing_entry_points_return_json_and_text(self):
        calls = (
            lambda **kw: self.provider.search_web(system_prompt="JSON", user_prompt="Search", **kw),
            lambda **kw: self.provider.inspect_website(url="https://example.com", instruction="JSON", **kw),
            lambda **kw: self.provider.generate(system_prompt="JSON", user_prompt="Draft", **kw),
        )
        for call in calls:
            self.create.return_value = completion('{"ok": true}')
            self.assertEqual(call(), {"ok": True})
            self.create.return_value = completion("Plain text")
            self.assertEqual(call(json_mode=False), "Plain text")
            self.assertNotIn("response_format", self.create.call_args.kwargs)
            for invalid in ("invalid", "[]"):
                self.create.return_value = completion(invalid)
                with self.assertRaises(RuntimeError):
                    call()


if __name__ == "__main__":
    unittest.main()
